"""
Shared UDP sender and mecanum kinematics — single source of truth.

Sends packets to universal_receiver.py via UDP.
Packet types:
  D  — drive (vx, vy, trans_speed, rot_speed)  → receiver does mixing
  W  — per-wheel velocities (node 0..3)
  S  — soft stop (zero velocity, stay armed)
  E  — emergency stop
"""

import math
import os
import socket
import struct
import time

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ── Motor configuration ──────────────────────────────────────────────
MOTORS = {
    0: {"role": "BL", "dir": -1},
    1: {"role": "FL", "dir": -1},
    2: {"role": "BR", "dir": 1},
    3: {"role": "FR", "dir": 1},
}
ALL_IDS = list(MOTORS.keys())
MAX_VEL = 10.0  # turns/s

# ── Physical constants ───────────────────────────────────────────────
WHEEL_DIAMETER = 9.55       # cm (effective — tuned empirically)
GEAR_RATIO = 16.0 / 90.0   # motor:wheel
CM_PER_MOTOR_REV = GEAR_RATIO * math.pi * WHEEL_DIAMETER  # ~6.424 cm
MOTOR_REVS_PER_CM = 1.0 / CM_PER_MOTOR_REV

TURNS_PER_DEG = 0.072       # motor turns per degree of robot rotation
MOVE_TIMEOUT = 30            # seconds — safety timeout
RAMP_PCT = 0.15              # ramp over first/last 15% of travel time
RAMP_MIN = 0.1               # minimum ramp fraction (10% of peak)
_RAMP_AVG = 1.0 - 2 * RAMP_PCT + 2 * RAMP_PCT * (RAMP_MIN + 1.0) / 2  # ~0.865
MOTOR_REVS_PER_CM_COMP = MOTOR_REVS_PER_CM / _RAMP_AVG  # ramp-compensated

# ── UDP socket ────────────────────────────────────────────────────────
UDP_HOST = os.getenv("ROBOT_IP", "127.0.0.1")
UDP_PORT = int(os.getenv("UDP_PORT", "5555"))
RELAY_PORT = int(os.getenv("RELAY_PORT", "5556"))

_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_udp_dest = (UDP_HOST, UDP_PORT)


def udp_dest():
    """Return the current UDP destination tuple."""
    return _udp_dest


def send_drive(vx, vy, trans_speed, rot_speed):
    """Send a 'D' drive packet (mecanum mixing done by receiver)."""
    _udp_sock.sendto(
        b"D" + struct.pack("<ffff", vx, vy, trans_speed, rot_speed), _udp_dest
    )


def send_wheels(wheel_vels):
    """Send a 'W' per-wheel velocity packet. wheel_vels: {nid: vel}."""
    data = struct.pack("<ffff", *(wheel_vels.get(nid, 0.0) for nid in range(4)))
    _udp_sock.sendto(b"W" + data, _udp_dest)


def send_stop():
    """Send an 'S' stop packet (zero velocity, stay armed)."""
    _udp_sock.sendto(b"S", _udp_dest)


def send_estop():
    """Send an 'E' e-stop packet."""
    _udp_sock.sendto(b"E", _udp_dest)


# ── Mecanum math ─────────────────────────────────────────────────────

def mecanum_speeds(vx, vy, omega):
    """Returns {node_id: speed} based on each motor's role."""
    roles = {
        "FL": vx - vy + omega,
        "FR": vx + vy - omega,
        "BL": vx + vy + omega,
        "BR": vx - vy - omega,
    }
    return {nid: roles[m["role"]] for nid, m in MOTORS.items()}


# ── Time-based moves ─────────────────────────────────────────────────

def run_move(targets, vel_pct, abort_event=None):
    """Time-based move with velocity ramp. Blocks until done or aborted.

    targets: {nid: total_turns_to_move} (signed, logical space)
    vel_pct: speed as percentage of MAX_VEL
    abort_event: optional threading.Event to abort early
    """
    if not targets:
        return

    vel_scale = (vel_pct / 100.0) * MAX_VEL
    max_travel = max(abs(t) for t in targets.values())
    if max_travel < 0.01:
        return

    T = max_travel / vel_scale
    peak = {nid: (targets[nid] / max_travel) * vel_scale for nid in targets}
    ramp_time = T * RAMP_PCT
    dt = 0.02  # 50 Hz
    start_time = time.time()

    try:
        while True:
            if abort_event and abort_event.is_set():
                break
            elapsed = time.time() - start_time
            if elapsed >= T or elapsed > MOVE_TIMEOUT:
                break

            remaining = T - elapsed
            if elapsed < ramp_time:
                ramp = RAMP_MIN + (1.0 - RAMP_MIN) * (elapsed / ramp_time)
            elif remaining < ramp_time:
                ramp = RAMP_MIN + (1.0 - RAMP_MIN) * (remaining / ramp_time)
            else:
                ramp = 1.0

            wheel_vels = {nid: peak[nid] * ramp for nid in targets}
            send_wheels(wheel_vels)
            time.sleep(dt)
    finally:
        send_stop()


def run_translate_rotate(distance_cm, heading_deg, rotation_deg, vel_pct,
                         abort_event=None):
    """Drive a straight world-frame line while simultaneously rotating.

    Micro-step approach: each 50 Hz tick computes translation and rotation
    wheel velocities separately, then combines them per-wheel. Heading
    compensation rotates the body-frame translation vector to maintain a
    straight world-frame path.
    """
    magnitude = distance_cm * MOTOR_REVS_PER_CM_COMP
    rot_turns = abs(rotation_deg) * TURNS_PER_DEG
    v = (vel_pct / 100.0) * MAX_VEL

    if v < 0.01:
        return

    # Pure rotation
    if magnitude < 0.01 and rot_turns > 0.01:
        rot_sign = 1.0 if rotation_deg >= 0 else -1.0
        rot_unit = mecanum_speeds(0, 0, rot_sign)
        max_r = max(abs(s) for s in rot_unit.values()) or 1.0
        targets = {nid: rot_unit[nid] / max_r * rot_turns for nid in ALL_IDS}
        run_move(targets, vel_pct, abort_event)
        return

    T = max(
        magnitude / v if magnitude > 0.01 else 0,
        rot_turns / v if rot_turns > 0.01 else 0,
    )
    if T < 0.01:
        return

    trans_rate = magnitude / T
    rot_rate = rot_turns / T
    rot_sign = 1.0 if rotation_deg >= 0 else -1.0
    omega_rad_per_s = -math.radians(rotation_deg) / T

    theta_world = math.radians(-heading_deg)
    vx_world = math.cos(theta_world)
    vy_world = math.sin(theta_world)

    rot_unit = mecanum_speeds(0, 0, rot_sign)
    max_r = max(abs(s) for s in rot_unit.values()) or 1.0

    dt = 0.02
    start_time = time.time()
    prev_time = start_time
    ramp_time = T * RAMP_PCT
    theta_accum = 0.0

    try:
        while True:
            if abort_event and abort_event.is_set():
                break
            now = time.time()
            elapsed = now - start_time
            dt_actual = now - prev_time
            prev_time = now

            if elapsed >= T or elapsed > MOVE_TIMEOUT:
                break

            remaining = T - elapsed
            if elapsed < ramp_time:
                ramp = RAMP_MIN + (1.0 - RAMP_MIN) * (elapsed / ramp_time)
            elif remaining < ramp_time:
                ramp = RAMP_MIN + (1.0 - RAMP_MIN) * (remaining / ramp_time)
            else:
                ramp = 1.0

            theta_accum += omega_rad_per_s * ramp * dt_actual

            cos_t = math.cos(theta_accum)
            sin_t = math.sin(theta_accum)
            vx_body = cos_t * vx_world + sin_t * vy_world
            vy_body = -sin_t * vx_world + cos_t * vy_world

            trans_unit = mecanum_speeds(vx_body, vy_body, 0)
            max_t = max(abs(s) for s in trans_unit.values()) or 1.0

            wheel_vels = {
                nid: (trans_unit[nid] / max_t) * trans_rate * ramp
                   + (rot_unit[nid] / max_r) * rot_rate * ramp
                for nid in ALL_IDS
            }
            send_wheels(wheel_vels)
            time.sleep(dt)
    finally:
        send_stop()
