"""Headless RL policy test simulator.

Skips camera/CSRT entirely — generates synthetic depth frames and state
vectors from a simulated 2D scene, runs the real policy, and applies
motor commands back to the sim. Opens a pygame window showing the
top-down scene so you can watch the robot react.

With --real-motors, sends actual D-packets over UDP to the Jetson's
universal_receiver.py so you can see real wheels spin on a stand.

Usage:
    python -m rl_policy.sim_test                           # sim only
    python -m rl_policy.sim_test --real-motors              # real wheels on stand
    python -m rl_policy.sim_test --real-motors --target-dist 0.3
    python -m rl_policy.sim_test --checkpoint rl_policy/checkpoints/rgbd_find.pt
"""

from __future__ import annotations

import argparse
import math
import os
import socket
import struct
import sys
import time

import cv2
import numpy as np
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from rl_policy.inference import PolicyRunner, IMG_SIZE, MAX_DEPTH_MM

# UDP for real motor output
UDP_HOST = os.getenv("ROBOT_IP", "192.168.2.83")
UDP_PORT = int(os.getenv("UDP_PORT", "5555"))

# ── Sim constants ────────────────────────────────────────────────────
SIM_HZ = 30
POLICY_HZ = 4
WINDOW_W, WINDOW_H = 1200, 700
SCENE_W = 500      # width of the 2D scene panel
WORLD_SCALE = 100  # pixels per meter

# Motor/robot constants (match real robot)
MAX_VEL_TURNS = 10.0       # ODrive max turns/s
WHEEL_RADIUS_M = 0.05      # mecanum wheel radius in meters
WHEELBASE_M = 0.3           # distance between left and right wheels
TRACK_M = 0.3               # distance between front and back wheels

# Action scaling (must match rl_track.py)
FORWARD_SCALE = 1.0
STRAFE_SCALE = 1.0
YAW_SCALE = 1.0
MAX_RANGE_SPEED = 10.0      # turns/s — full ODrive range
MAX_ROT_SPEED = 10.0        # turns/s — full ODrive range


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class SimRobot:
    """2D robot with mecanum kinematics."""

    def __init__(self, x: float = 0.0, y: float = 0.0, heading: float = 0.0):
        self.x = x          # meters
        self.y = y          # meters
        self.heading = heading  # radians, 0 = facing +X
        self.vx_body = 0.0  # m/s in body frame
        self.vy_body = 0.0
        self.omega = 0.0    # rad/s

        # Individual wheel speeds for display (turns/s)
        self.wheel_speeds = {"FL": 0.0, "FR": 0.0, "BL": 0.0, "BR": 0.0}

    def apply_motor_command(self, vx_dir, vy_dir, trans_speed, rot_speed, dt):
        """Apply D-packet style command. trans_speed/rot_speed in turns/s."""
        # Convert turns/s to m/s
        trans_ms = trans_speed * WHEEL_RADIUS_M * 2 * math.pi
        rot_rads = rot_speed * WHEEL_RADIUS_M * 2 * math.pi / (WHEELBASE_M / 2)

        # Body-frame velocities
        self.vx_body = vx_dir * trans_ms
        self.vy_body = vy_dir * trans_ms
        self.omega = rot_rads

        # Compute per-wheel speeds for display (mecanum kinematics, turns/s)
        # vx_dir and vy_dir are unit vector components
        raw_fl = vx_dir - vy_dir
        raw_fr = vx_dir + vy_dir
        raw_bl = vx_dir + vy_dir
        raw_br = vx_dir - vy_dir
        max_raw = max(abs(raw_fl), abs(raw_fr), abs(raw_bl), abs(raw_br), 1e-6)
        self.wheel_speeds["FL"] = (raw_fl / max_raw) * trans_speed + rot_speed
        self.wheel_speeds["FR"] = (raw_fr / max_raw) * trans_speed - rot_speed
        self.wheel_speeds["BL"] = (raw_bl / max_raw) * trans_speed + rot_speed
        self.wheel_speeds["BR"] = (raw_br / max_raw) * trans_speed - rot_speed

        # Integrate in world frame
        cos_h = math.cos(self.heading)
        sin_h = math.sin(self.heading)
        wx = self.vx_body * cos_h - self.vy_body * sin_h
        wy = self.vx_body * sin_h + self.vy_body * cos_h
        self.x += wx * dt
        self.y += wy * dt
        self.heading += self.omega * dt

    def stop(self):
        self.vx_body = 0.0
        self.vy_body = 0.0
        self.omega = 0.0
        self.wheel_speeds = {k: 0.0 for k in self.wheel_speeds}


class SimTarget:
    """Movable target in the world."""

    def __init__(self, x: float = 2.0, y: float = 0.0):
        self.x = x
        self.y = y
        self.dragging = False


def scale_actions(raw_actions):
    """Same as rl_track.py _scale_actions — keep in sync."""
    if len(raw_actions) == 2:
        fwd = raw_actions[0] * FORWARD_SCALE
        strafe = 0.0
        yaw = raw_actions[1] * YAW_SCALE
    else:
        fwd = raw_actions[0] * FORWARD_SCALE
        strafe = raw_actions[1] * STRAFE_SCALE
        yaw = raw_actions[2] * YAW_SCALE

    magnitude = math.sqrt(fwd ** 2 + strafe ** 2)
    if magnitude > 0.01:
        vx = fwd / magnitude
        vy = strafe / magnitude
        trans_speed = min(magnitude, 1.0) * MAX_RANGE_SPEED
    else:
        vx = 0.0
        vy = 0.0
        trans_speed = 0.0

    rot_speed = _clamp(yaw * MAX_ROT_SPEED, -MAX_ROT_SPEED, MAX_ROT_SPEED)
    return vx, vy, trans_speed, rot_speed


ROOM_HALF_W = 3.0   # meters, room is 6m wide
ROOM_DEPTH = 8.0    # meters, back wall distance
CAMERA_H = 0.3      # meters, camera height above floor
DEPTH_VIS_MAX = 5000 # mm, colormap ceiling for visualization

def generate_synthetic_depth(robot: SimRobot, target: SimTarget,
                             img_w: int = 320, img_h: int = 240) -> np.ndarray:
    """Generate a synthetic depth frame with floor, walls, and target."""
    fov_h_rad = math.radians(87)
    fov_v_rad = math.radians(58)
    tan_hh = math.tan(fov_h_rad / 2)
    tan_vh = math.tan(fov_v_rad / 2)

    cos_h = math.cos(-robot.heading)
    sin_h = math.sin(-robot.heading)

    # Per-pixel ray directions in camera frame
    u = np.linspace(-tan_hh, tan_hh, img_w).astype(np.float32)
    v = np.linspace(-tan_vh, tan_vh, img_h).astype(np.float32)
    uu, vv = np.meshgrid(u, v)
    # ray_cam: (forward=1, right=uu, down=vv)

    # Floor: y_world = 0, camera at CAMERA_H
    # In camera frame, floor hit when vv * z = CAMERA_H → z = CAMERA_H / vv
    floor_depth = np.full((img_h, img_w), 30000, dtype=np.float32)
    floor_mask = vv > 0.01
    floor_depth[floor_mask] = CAMERA_H / vv[floor_mask]  # meters forward
    floor_depth_mm = (floor_depth * 1000).astype(np.uint16)

    # Back wall: a wall at ROOM_DEPTH in front of the robot (world frame)
    # Transform wall to robot body frame
    wall_depth = np.full((img_h, img_w), 30000, dtype=np.uint16)
    wall_z = ROOM_DEPTH  # meters forward
    wall_depth[:] = int(wall_z * 1000)

    # Side walls: left wall and right wall at ±ROOM_HALF_W in world frame
    # In camera frame, side wall at local_y = ±d hits when uu * z = ±d → z = d / |uu|
    for sign in [-1, 1]:
        wall_world_y = sign * ROOM_HALF_W
        # Transform to robot local frame
        local_y = -robot.x * sin_h + (wall_world_y - robot.y) * cos_h
        side_mask = (sign * uu) > 0.01
        side_z = np.full((img_h, img_w), 30000.0, dtype=np.float32)
        side_z[side_mask] = abs(local_y) / np.abs(uu[side_mask])
        side_z_mm = np.clip(side_z * 1000, 0, 30000).astype(np.uint16)
        wall_depth = np.minimum(wall_depth, side_z_mm)

    # Composite: take nearest surface
    depth = np.minimum(floor_depth_mm, wall_depth)

    # Target object
    dx = target.x - robot.x
    dy = target.y - robot.y
    local_x = dx * cos_h - dy * sin_h
    local_y = dx * sin_h + dy * cos_h
    dist = max(math.sqrt(dx ** 2 + dy ** 2), 0.05)

    if local_x > 0.01:
        cx_img = img_w / 2 + (local_y / local_x) * (img_w / 2) / tan_hh
        cy_img = img_h / 2
        target_size_m = 0.3
        target_size_px = int((target_size_m / dist) * (img_w / 2) / tan_hh)
        target_size_px = max(5, min(target_size_px, img_w))
        depth_mm = int(dist * 1000)
        cx_i, cy_i = int(cx_img), int(cy_img)
        r = target_size_px // 2
        yy, xx = np.ogrid[0:img_h, 0:img_w]
        mask = ((xx - cx_i) ** 2 + (yy - cy_i) ** 2) <= r * r
        depth[mask] = depth_mm

    return depth


def generate_synthetic_rgb(robot: SimRobot, target: SimTarget,
                           img_w: int = 320, img_h: int = 240) -> np.ndarray:
    """Generate a synthetic RGB frame with green target overlay."""
    frame = np.zeros((img_h, img_w, 3), dtype=np.uint8)
    frame[:] = (60, 60, 60)  # gray background

    dx = target.x - robot.x
    dy = target.y - robot.y
    cos_h = math.cos(-robot.heading)
    sin_h = math.sin(-robot.heading)
    local_x = dx * cos_h - dy * sin_h
    local_y = dx * sin_h + dy * cos_h
    dist = max(math.sqrt(dx ** 2 + dy ** 2), 0.05)

    if local_x > 0.01:
        fov_h = 87
        cx_img = img_w / 2 + (local_y / local_x) * (img_w / 2) / math.tan(math.radians(fov_h / 2))
        cy_img = img_h / 2
        target_size_m = 0.3
        target_size_px = int((target_size_m / dist) * (img_w / 2) / math.tan(math.radians(fov_h / 2)))
        target_size_px = max(5, min(target_size_px, img_w))
        r = target_size_px // 2
        cv2.rectangle(frame,
                      (int(cx_img) - r, int(cy_img) - r),
                      (int(cx_img) + r, int(cy_img) + r),
                      (0, 255, 0), -1)

    return frame


def compute_bbox(robot: SimRobot, target: SimTarget, img_w=320, img_h=240):
    """Return (cx, cy, w, h) normalized, or None if target not visible."""
    dx = target.x - robot.x
    dy = target.y - robot.y
    cos_h = math.cos(-robot.heading)
    sin_h = math.sin(-robot.heading)
    local_x = dx * cos_h - dy * sin_h
    local_y = dx * sin_h + dy * cos_h
    dist = max(math.sqrt(dx ** 2 + dy ** 2), 0.05)

    if local_x <= 0.01:
        return None

    fov_h = 87
    cx_norm = 0.5 + (local_y / local_x) / (2 * math.tan(math.radians(fov_h / 2)))
    cy_norm = 0.5
    target_size_m = 0.3
    w_norm = (target_size_m / dist) / (2 * math.tan(math.radians(fov_h / 2)))
    h_norm = w_norm * (img_w / img_h)

    if cx_norm < -0.1 or cx_norm > 1.1:
        return None

    class BBox:
        pass
    b = BBox()
    b.cx = _clamp(cx_norm, 0, 1)
    b.cy = cy_norm
    b.w = min(w_norm, 1.0)
    b.h = min(h_norm, 1.0)
    b.confidence = 0.95
    return b


def generate_lidar_scan(robot: SimRobot, target: SimTarget, num_points=100):
    """Generate a fake lidar scan — empty except for the target."""
    points = []
    dx = target.x - robot.x
    dy = target.y - robot.y
    dist = math.sqrt(dx ** 2 + dy ** 2)
    angle = math.degrees(math.atan2(dy, dx) - robot.heading) % 360

    # Put a cluster of points around the target
    for i in range(-5, 6):
        a = (angle + i * 0.5) % 360
        d = dist + np.random.uniform(-0.02, 0.02)
        points.append({"angle": a, "dist_mm": max(10, d * 1000)})
    return points


def draw_scene(screen, robot: SimRobot, target: SimTarget,
               raw_actions, motor_cmd, desired_dist, info_lines,
               depth_frame=None, lidar_scan=None):
    """Draw 2D top-down scene with pygame."""
    import pygame

    screen.fill((30, 30, 30))

    ox = SCENE_W // 2
    oy = WINDOW_H // 2

    def world_to_screen(wx, wy):
        return (int(ox + wx * WORLD_SCALE), int(oy - wy * WORLD_SCALE))

    # Grid
    for gx in range(-5, 6):
        sx, _ = world_to_screen(gx, 0)
        pygame.draw.line(screen, (50, 50, 50), (sx, 0), (sx, WINDOW_H))
    for gy in range(-4, 5):
        _, sy = world_to_screen(0, gy)
        pygame.draw.line(screen, (50, 50, 50), (0, sy), (SCENE_W, sy))

    # Distance rings from robot
    rpos = world_to_screen(robot.x, robot.y)
    for d in [0.5, 1.0, 1.5, 2.0, 3.0]:
        r = int(d * WORLD_SCALE)
        pygame.draw.circle(screen, (40, 40, 50), rpos, r, 1)

    # Desired distance ring
    dr = int(desired_dist * WORLD_SCALE)
    pygame.draw.circle(screen, (0, 100, 200), rpos, dr, 2)

    # Robot body
    robot_size = 0.15  # meters half-size
    corners_body = [
        (-robot_size, -robot_size),
        (robot_size, -robot_size),
        (robot_size, robot_size),
        (-robot_size, robot_size),
    ]
    cos_h = math.cos(robot.heading)
    sin_h = math.sin(robot.heading)
    corners_world = []
    for bx, by in corners_body:
        wx = robot.x + bx * cos_h - by * sin_h
        wy = robot.y + bx * sin_h + by * cos_h
        corners_world.append(world_to_screen(wx, wy))
    pygame.draw.polygon(screen, (0, 150, 255), corners_world, 0)
    pygame.draw.polygon(screen, (0, 200, 255), corners_world, 2)

    # Robot heading arrow
    arrow_len = 0.25
    ax = robot.x + arrow_len * cos_h
    ay = robot.y + arrow_len * sin_h
    pygame.draw.line(screen, (255, 255, 0), rpos, world_to_screen(ax, ay), 3)

    # Target
    tpos = world_to_screen(target.x, target.y)
    pygame.draw.circle(screen, (0, 255, 0), tpos, 12, 0)
    pygame.draw.circle(screen, (255, 255, 255), tpos, 12, 2)

    # Distance line
    dist = math.sqrt((target.x - robot.x) ** 2 + (target.y - robot.y) ** 2)
    pygame.draw.line(screen, (100, 100, 100), rpos, tpos, 1)

    # Velocity arrow from robot
    vel_scale = 50
    vx_world = robot.vx_body * cos_h - robot.vy_body * sin_h
    vy_world = robot.vx_body * sin_h + robot.vy_body * cos_h
    vel_end = (int(rpos[0] + vx_world * vel_scale),
               int(rpos[1] - vy_world * vel_scale))
    pygame.draw.line(screen, (255, 100, 0), rpos, vel_end, 3)

    # ── Center panel: info ──
    panel_x = SCENE_W + 20
    font = pygame.font.SysFont("consolas", 16)
    font_big = pygame.font.SysFont("consolas", 20, bold=True)

    y = 20
    def text(txt, color=(220, 220, 220), big=False):
        nonlocal y
        f = font_big if big else font
        surf = f.render(txt, True, color)
        screen.blit(surf, (panel_x, y))
        y += surf.get_height() + 4

    text("RL POLICY SIM TEST", (0, 200, 255), big=True)
    y += 10
    text(f"Distance: {dist:.3f} m", (255, 255, 100))
    text(f"Desired:  {desired_dist:.2f} m", (0, 150, 255))
    text(f"Error:    {dist - desired_dist:+.3f} m",
         (0, 255, 0) if abs(dist - desired_dist) < 0.1 else (255, 100, 100))
    y += 10

    text("── Raw Policy Output ──", (150, 150, 150))
    labels = ["fwd", "strafe", "yaw"] if len(raw_actions) >= 3 else ["fwd", "yaw"]
    for i, lbl in enumerate(labels):
        val = raw_actions[i] if i < len(raw_actions) else 0
        bar_w = int(abs(val) * 100)
        color = (0, 255, 100) if val >= 0 else (255, 80, 80)
        text(f"  {lbl:>6}: {val:+.4f}", color)
        bar_y = y - 14
        bar_x = panel_x + 220
        pygame.draw.rect(screen, color,
                         (bar_x if val >= 0 else bar_x - bar_w, bar_y, bar_w, 10))
    y += 10

    text("── Motor Command ──", (150, 150, 150))
    vx, vy, ts, rs = motor_cmd
    text(f"  vx:    {vx:+.3f}", (200, 200, 200))
    text(f"  vy:    {vy:+.3f}", (200, 200, 200))
    text(f"  trans: {ts:+.3f} t/s", (255, 200, 0))
    text(f"  rot:   {rs:+.3f} t/s", (255, 200, 0))
    y += 10

    text("── Wheel Speeds (turns/s) ──", (150, 150, 150))
    for role in ["FL", "FR", "BL", "BR"]:
        ws = robot.wheel_speeds[role]
        color = (0, 255, 100) if ws >= 0 else (255, 80, 80)
        text(f"  {role}: {ws:+.2f}", color)
    y += 10

    text("── Controls ──", (150, 150, 150))
    text("  Drag green target with mouse", (180, 180, 180))
    text("  R = reset robot position", (180, 180, 180))
    text("  +/- = change desired dist", (180, 180, 180))
    text("  SPACE = pause/resume", (180, 180, 180))
    text("  Q/ESC = quit", (180, 180, 180))

    for line in info_lines:
        text(line, (150, 150, 150))

    # Divider between scene and info
    pygame.draw.line(screen, (80, 80, 80),
                     (SCENE_W, 0), (SCENE_W, WINDOW_H), 1)

    # ── Right panel: sensor feeds ──
    FEED_X = 860
    pygame.draw.line(screen, (80, 80, 80),
                     (FEED_X - 10, 0), (FEED_X - 10, WINDOW_H), 1)

    feed_font = pygame.font.SysFont("consolas", 14)

    # Depth frame (colorized)
    if depth_frame is not None:
        label = feed_font.render("DEPTH FEED", True, (0, 200, 255))
        screen.blit(label, (FEED_X, 10))
        depth_clipped = np.clip(depth_frame, 0, DEPTH_VIS_MAX).astype(np.float32)
        depth_norm = (depth_clipped / DEPTH_VIS_MAX * 255).astype(np.uint8)
        depth_color = cv2.applyColorMap(depth_norm, cv2.COLORMAP_TURBO)
        depth_color = cv2.cvtColor(depth_color, cv2.COLOR_BGR2RGB)
        dh, dw = depth_color.shape[:2]
        display_w = WINDOW_W - FEED_X - 10
        scale = display_w / dw
        display_h = int(dh * scale)
        depth_resized = cv2.resize(depth_color, (display_w, display_h))
        surf = pygame.surfarray.make_surface(depth_resized.swapaxes(0, 1))
        screen.blit(surf, (FEED_X, 30))
        dist_text = math.sqrt((target.x - robot.x) ** 2 + (target.y - robot.y) ** 2)
        depth_info = feed_font.render(f"{dist_text:.2f}m", True, (200, 200, 200))
        screen.blit(depth_info, (FEED_X, 32 + display_h))
        feed_y_after_depth = 50 + display_h
    else:
        feed_y_after_depth = 30

    # Lidar polar plot
    if lidar_scan is not None and len(lidar_scan) > 0:
        lidar_y = feed_y_after_depth + 20
        label = feed_font.render("LIDAR FEED", True, (0, 255, 100))
        screen.blit(label, (FEED_X, lidar_y))
        lidar_y += 20

        plot_size = min(WINDOW_W - FEED_X - 10, WINDOW_H - lidar_y - 10)
        plot_cx = FEED_X + plot_size // 2
        plot_cy = lidar_y + plot_size // 2
        max_range_mm = 5000.0
        plot_r = plot_size // 2 - 5

        # Range rings
        for ring_m in [1.0, 2.0, 3.0, 4.0]:
            rr = int(ring_m * 1000 / max_range_mm * plot_r)
            pygame.draw.circle(screen, (40, 50, 40), (plot_cx, plot_cy), rr, 1)
        # Cross hairs
        pygame.draw.line(screen, (40, 50, 40),
                         (plot_cx - plot_r, plot_cy), (plot_cx + plot_r, plot_cy), 1)
        pygame.draw.line(screen, (40, 50, 40),
                         (plot_cx, plot_cy - plot_r), (plot_cx, plot_cy + plot_r), 1)

        # Robot marker
        pygame.draw.circle(screen, (0, 150, 255), (plot_cx, plot_cy), 4, 0)

        # Scan points
        for pt in lidar_scan:
            a_rad = math.radians(pt["angle"])
            d_mm = pt["dist_mm"]
            r_px = min(d_mm / max_range_mm, 1.0) * plot_r
            px = int(plot_cx + r_px * math.cos(a_rad))
            py = int(plot_cy - r_px * math.sin(a_rad))
            pygame.draw.circle(screen, (0, 255, 80), (px, py), 3, 0)

        # Range labels
        for ring_m in [1, 2, 3, 4]:
            rr = int(ring_m * 1000 / max_range_mm * plot_r)
            lbl = feed_font.render(f"{ring_m}m", True, (80, 80, 80))
            screen.blit(lbl, (plot_cx + 3, plot_cy - rr - 12))

    pygame.display.flip()


def main():
    parser = argparse.ArgumentParser(description="RL policy sim tester")
    parser.add_argument("--checkpoint", default="",
                        help="Path to .pt checkpoint (default: auto-detect)")
    parser.add_argument("--desired-dist", type=float, default=1.0,
                        help="Target follow distance in meters")
    parser.add_argument("--target-dist", type=float, default=2.0,
                        help="Initial target distance in meters")
    parser.add_argument("--device", default="cpu",
                        help="Torch device (cpu or cuda)")
    parser.add_argument("--real-motors", action="store_true",
                        help="Send real D-packets over UDP to Jetson")
    parser.add_argument("--robot-ip", default=UDP_HOST,
                        help=f"Jetson IP for real motors (default: {UDP_HOST})")
    parser.add_argument("--udp-port", type=int, default=UDP_PORT,
                        help=f"UDP port (default: {UDP_PORT})")
    args = parser.parse_args()

    # UDP socket for real motors
    udp_sock = None
    udp_dest = None
    if args.real_motors:
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_dest = (args.robot_ip, args.udp_port)
        print(f"REAL MOTORS enabled → sending D-packets to {udp_dest[0]}:{udp_dest[1]}")

    import pygame
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    title = "RL Policy Sim Test"
    if args.real_motors:
        title += " [REAL MOTORS]"
    pygame.display.set_caption(title)

    # Find checkpoint
    ckpt = args.checkpoint
    if not ckpt:
        ckpt_dir = os.path.join(os.path.dirname(__file__), "checkpoints")
        candidates = sorted(
            f for f in os.listdir(ckpt_dir)
            if f.endswith(".pt") and "_traced" not in f
        )
        if not candidates:
            print("No checkpoints found!")
            return
        ckpt = os.path.join(ckpt_dir, candidates[0])
    print(f"Loading: {ckpt}")

    runner = PolicyRunner(ckpt, device=args.device)
    variant = ("depth" if runner.model.cnn_channels == 1 else "rgbd") + f"_{runner.action_dim}a"
    info_lines = [f"Model: {os.path.basename(ckpt)}", f"Variant: {variant}"]
    if runner.has_lidar:
        info_lines.append("LiDAR: yes (synthetic)")
    if args.real_motors:
        info_lines.append(f"UDP: {args.robot_ip}:{args.udp_port}")

    robot = SimRobot(x=0.0, y=0.0, heading=0.0)
    target = SimTarget(x=args.target_dist, y=0.0)
    desired_dist = args.desired_dist

    clock = pygame.time.Clock()
    policy_interval = 1.0 / POLICY_HZ
    last_policy_tick = 0.0
    raw_actions = [0.0, 0.0, 0.0]
    motor_cmd = (0.0, 0.0, 0.0, 0.0)
    last_depth = generate_synthetic_depth(robot, target)
    last_scan = generate_lidar_scan(robot, target) if runner.has_lidar else []
    paused = False
    running = True

    while running:
        dt = clock.tick(SIM_HZ) / 1000.0
        now = time.time()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False
                elif event.key == pygame.K_r:
                    robot = SimRobot(0.0, 0.0, 0.0)
                    runner.reset()
                    raw_actions = [0.0] * runner.action_dim
                    motor_cmd = (0.0, 0.0, 0.0, 0.0)
                    if udp_sock:
                        udp_sock.sendto(b"S", udp_dest)
                elif event.key == pygame.K_SPACE:
                    paused = not paused
                    if paused:
                        robot.stop()
                        if udp_sock:
                            udp_sock.sendto(b"S", udp_dest)
                elif event.key == pygame.K_PLUS or event.key == pygame.K_EQUALS:
                    desired_dist = min(desired_dist + 0.1, 5.0)
                elif event.key == pygame.K_MINUS:
                    desired_dist = max(desired_dist - 0.1, 0.1)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    # Check if clicking near target
                    _ox = SCENE_W // 2
                    _oy = WINDOW_H // 2
                    mx, my = event.pos
                    tw = int(_ox + target.x * WORLD_SCALE)
                    th = int(_oy - target.y * WORLD_SCALE)
                    if abs(mx - tw) < 20 and abs(my - th) < 20:
                        target.dragging = True
            elif event.type == pygame.MOUSEBUTTONUP:
                target.dragging = False
            elif event.type == pygame.MOUSEMOTION:
                if target.dragging:
                    _ox = SCENE_W // 2
                    _oy = WINDOW_H // 2
                    mx, my = event.pos
                    target.x = (mx - _ox) / WORLD_SCALE
                    target.y = -(my - _oy) / WORLD_SCALE

        if not paused and now - last_policy_tick >= policy_interval:
            # Generate synthetic inputs
            depth = generate_synthetic_depth(robot, target)
            rgb = generate_synthetic_rgb(robot, target)
            bbox = compute_bbox(robot, target)
            scan = generate_lidar_scan(robot, target) if runner.has_lidar else []
            last_depth = depth
            last_scan = scan

            if bbox is not None:
                raw_actions = runner.step(rgb, depth, bbox, scan, desired_dist)
                vx, vy, ts, rs = scale_actions(raw_actions)
                motor_cmd = (vx, vy, ts, rs)

                if udp_sock and (abs(ts) > 0.01 or abs(rs) > 0.01):
                    pkt = b"D" + struct.pack("<ffff", vx, vy, ts, rs)
                    udp_sock.sendto(pkt, udp_dest)
                elif udp_sock:
                    udp_sock.sendto(b"S", udp_dest)
            else:
                raw_actions = [0.0] * runner.action_dim
                motor_cmd = (0.0, 0.0, 0.0, 0.0)
                runner.reset()
                if udp_sock:
                    udp_sock.sendto(b"S", udp_dest)

            last_policy_tick = now

        # Apply motor command to sim
        if not paused:
            robot.apply_motor_command(*motor_cmd, dt)

        # Regenerate sensor feeds every frame so they track robot movement
        last_depth = generate_synthetic_depth(robot, target)
        if runner.has_lidar:
            last_scan = generate_lidar_scan(robot, target)

        draw_scene(screen, robot, target, raw_actions, motor_cmd,
                   desired_dist, info_lines,
                   depth_frame=last_depth, lidar_scan=last_scan)

    # Stop motors on exit
    if udp_sock:
        udp_sock.sendto(b"S", udp_dest)
        udp_sock.close()
    pygame.quit()


if __name__ == "__main__":
    main()
