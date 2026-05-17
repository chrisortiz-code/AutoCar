"""
Jetson local camera color tracking -> CAN motor control.

Run this on the Jetson with the webcam plugged into the Jetson.

Usage:
    python cam_jetson_controller.py
    python cam_jetson_controller.py --camera 1

Controls:
    Click      pick target color from the live camera feed
    SPACE      start tracking the picked color
    R          re-pick color
    S          stop motors, keep selected color
    Q / ESC    quit

The controller thresholds pixels near the selected HSV color, cleans the mask,
finds the largest target blob, and drives sideways/forward/backward until that
blob's centroid and apparent area match the picked target.
"""

import argparse
import atexit
import platform
import signal
import time

import cv2
import numpy as np

from can_bus import MecanumCAN


# Color matching. These are intentionally broad so more of the target object is
# included in the detected contour instead of only the exact clicked shade.
HSV_TOL_H = 35
HSV_TOL_S = 100
HSV_TOL_V = 100

# Tracking and safety.
DEADZONE = 0.10      # center 10% of frame means no sideways correction
AREA_DEADZONE = 0.18 # area can vary this much before forward/back correction
AREA_GAIN = 0.70     # larger values make area/distance correction softer
EDGE_THRESHOLD = 0.78
EDGE_REACQUIRE_SECONDS = 1.2
MAX_STRAFE = 3.0     # turns/s at full camera offset
MAX_RANGE_SPEED = 2.0
MIN_BLOB = 500       # minimum matching contour area in pixels
CONTROL_HZ = 20
SAMPLE_SIZE = 5
DEFAULT_TARGET_RGB = (36, 89, 133)


picked_hsv = None
picked_rgb = None
target_area = None
tracking = False
click_pos = None


def parse_args():
    parser = argparse.ArgumentParser(description="Jetson local camera color controller")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--max-strafe", type=float, default=MAX_STRAFE, help="Max strafe speed in turns/s")
    parser.add_argument("--max-range-speed", type=float, default=MAX_RANGE_SPEED, help="Max forward/back speed in turns/s")
    parser.add_argument("--deadzone", type=float, default=DEADZONE, help="Centered deadzone as fraction of frame width")
    parser.add_argument("--area-deadzone", type=float, default=AREA_DEADZONE, help="Accepted area error before forward/back correction")
    parser.add_argument("--area-gain", type=float, default=AREA_GAIN, help="Area error that maps to full forward/back command")
    parser.add_argument("--edge-threshold", type=float, default=EDGE_THRESHOLD, help="Horizontal error that counts as near the frame edge")
    parser.add_argument("--edge-reacquire-seconds", type=float, default=EDGE_REACQUIRE_SECONDS, help="How long to keep moving after losing an edge target")
    parser.add_argument("--h-tol", type=int, default=HSV_TOL_H, help="HSV hue tolerance")
    parser.add_argument("--s-tol", type=int, default=HSV_TOL_S, help="HSV saturation tolerance")
    parser.add_argument("--v-tol", type=int, default=HSV_TOL_V, help="HSV value tolerance")
    parser.add_argument("--min-blob", type=float, default=MIN_BLOB, help="Minimum contour area to accept target")
    parser.add_argument(
        "--target-rgb",
        default=None,
        help="Hardcoded target RGB as R,G,B. Example: --target-rgb 36,89,133",
    )
    parser.add_argument(
        "--use-default-target",
        action="store_true",
        help=f"Use built-in target RGB {DEFAULT_TARGET_RGB[0]},{DEFAULT_TARGET_RGB[1]},{DEFAULT_TARGET_RGB[2]}",
    )
    parser.add_argument("--no-gui", action="store_true", help="Run without OpenCV GUI; requires a hardcoded target")
    parser.add_argument("--dry-run", action="store_true", help="Show camera and print commands without using CAN")
    return parser.parse_args()


def parse_rgb(text):
    try:
        parts = [int(part.strip()) for part in text.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("RGB must be three integers: R,G,B") from exc
    if len(parts) != 3 or any(part < 0 or part > 255 for part in parts):
        raise argparse.ArgumentTypeError("RGB must be three integers from 0 to 255: R,G,B")
    return tuple(parts)


def rgb_to_hsv(rgb):
    r, g, b = rgb
    bgr = np.array([[[b, g, r]]], dtype=np.uint8)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[0, 0]
    return tuple(int(v) for v in hsv)


def clamp(value, low, high):
    return max(low, min(high, value))


def on_mouse(event, x, y, flags, param):
    global click_pos
    if event == cv2.EVENT_LBUTTONDOWN:
        click_pos = (x, y)


def sample_color(frame_bgr, x, y):
    h, w = frame_bgr.shape[:2]
    half = SAMPLE_SIZE // 2
    x0 = max(0, x - half)
    x1 = min(w, x + half + 1)
    y0 = max(0, y - half)
    y1 = min(h, y + half + 1)

    region = frame_bgr[y0:y1, x0:x1]
    avg_bgr = region.mean(axis=(0, 1)).astype(np.uint8)
    avg_hsv = cv2.cvtColor(avg_bgr.reshape(1, 1, 3), cv2.COLOR_BGR2HSV)[0, 0]
    avg_rgb = (int(avg_bgr[2]), int(avg_bgr[1]), int(avg_bgr[0]))
    return tuple(int(v) for v in avg_hsv), avg_rgb


def color_mask(frame_bgr, hsv_center, h_tol, s_tol, v_tol):
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv_center

    lower = np.array([max(0, h - h_tol), max(0, s - s_tol), max(0, v - v_tol)])
    upper = np.array([min(179, h + h_tol), min(255, s + s_tol), min(255, v + v_tol)])

    if h - h_tol < 0:
        mask1 = cv2.inRange(hsv, np.array([0, lower[1], lower[2]]), upper)
        mask2 = cv2.inRange(
            hsv,
            np.array([180 + h - h_tol, lower[1], lower[2]]),
            np.array([179, upper[1], upper[2]]),
        )
        mask = cv2.bitwise_or(mask1, mask2)
    elif h + h_tol > 179:
        mask1 = cv2.inRange(hsv, lower, np.array([179, upper[1], upper[2]]))
        mask2 = cv2.inRange(
            hsv,
            np.array([0, lower[1], lower[2]]),
            np.array([h + h_tol - 180, upper[1], upper[2]]),
        )
        mask = cv2.bitwise_or(mask1, mask2)
    else:
        mask = cv2.inRange(hsv, lower, upper)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def find_target(frame_bgr, hsv_center, min_blob, h_tol, s_tol, v_tol):
    mask = color_mask(frame_bgr, hsv_center, h_tol, s_tol, v_tol)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, mask

    biggest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(biggest)
    if area < min_blob:
        return None, mask

    moments = cv2.moments(biggest)
    if moments["m00"] == 0:
        return None, mask

    cx = int(moments["m10"] / moments["m00"])
    cy = int(moments["m01"] / moments["m00"])
    return {"x": cx, "y": cy, "area": area, "contour": biggest}, mask


def pick_target_area(frame_bgr, hsv_center, min_blob, h_tol, s_tol, v_tol):
    target, _ = find_target(frame_bgr, hsv_center, min_blob, h_tol, s_tol, v_tol)
    if target:
        return target["area"]
    return None


def draw_overlay(frame, deadzone, command_text):
    fh, fw = frame.shape[:2]
    center_x = fw // 2
    dz_px = int(fw * deadzone / 2)

    cv2.line(frame, (center_x, 0), (center_x, fh), (50, 50, 50), 1)
    cv2.line(frame, (center_x - dz_px, 0), (center_x - dz_px, fh), (50, 50, 50), 1)
    cv2.line(frame, (center_x + dz_px, 0), (center_x + dz_px, fh), (50, 50, 50), 1)

    if picked_rgb:
        cv2.rectangle(frame, (10, 10), (50, 50), picked_rgb[::-1], -1)
        cv2.rectangle(frame, (10, 10), (50, 50), (255, 255, 255), 1)
        cv2.putText(
            frame,
            f"RGB({picked_rgb[0]},{picked_rgb[1]},{picked_rgb[2]})",
            (58, 38),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
        )

    if command_text:
        cv2.putText(frame, command_text, (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

    if not picked_hsv:
        status = "Click target color"
        color = (0, 255, 255)
    elif tracking:
        status = "TRACKING | S=stop R=re-pick Q=quit"
        color = (0, 255, 0)
    else:
        status = "SPACE=start tracking | R=re-pick Q=quit"
        color = (0, 255, 255)
    cv2.putText(frame, status, (10, fh - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)


def stop_drive(mc, dry_run):
    if dry_run:
        print("STOP")
    else:
        mc.zero_vel()


def drive_vector(mc, dry_run, vx, vy, speed):
    if dry_run:
        print(f"DRIVE vx={vx:+.2f} vy={vy:+.2f} trans_speed={speed:.2f} rot=0.00")
    else:
        mc.drive(vx, vy, speed, 0.0)


def main():
    global picked_hsv, picked_rgb, target_area, tracking, click_pos

    args = parse_args()
    state = {"mc": None, "cap": None, "driving": False, "cleaned": False}

    def cleanup():
        if state["cleaned"]:
            return
        state["cleaned"] = True
        if state["driving"] and state["mc"]:
            stop_drive(state["mc"], args.dry_run)
        if state["cap"]:
            state["cap"].release()
        if not args.no_gui:
            cv2.destroyAllWindows()
        if state["mc"]:
            state["mc"].shutdown()

    def handle_signal(signum, frame):
        cleanup()
        raise KeyboardInterrupt

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    hardcoded_rgb = None
    if args.use_default_target:
        hardcoded_rgb = DEFAULT_TARGET_RGB
    if args.target_rgb:
        try:
            hardcoded_rgb = parse_rgb(args.target_rgb)
        except argparse.ArgumentTypeError as exc:
            print(f"Invalid --target-rgb: {exc}")
            return
    if args.no_gui and hardcoded_rgb is None:
        print("--no-gui requires --target-rgb R,G,B or --use-default-target")
        return

    if hardcoded_rgb is not None:
        picked_rgb = hardcoded_rgb
        picked_hsv = rgb_to_hsv(hardcoded_rgb)
        tracking = True
        print(f"Using target RGB{picked_rgb} HSV{picked_hsv}")

    if platform.system() == "Linux":
        cap = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
    else:
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Cannot open camera index {args.camera}")
        return
    state["cap"] = cap

    if not args.no_gui:
        try:
            cv2.namedWindow("Jetson Camera Control")
            cv2.setMouseCallback("Jetson Camera Control", on_mouse)
        except cv2.error:
            cap.release()
            raise

    mc = None
    if not args.dry_run:
        mc = MecanumCAN(current_limit=30.0)
        state["mc"] = mc
        mc.connect()
        if mc.bus is None:
            cap.release()
            if not args.no_gui:
                cv2.destroyAllWindows()
            print("CAN is unavailable. Exiting before enabling camera control.")
            return
        if not mc.connected:
            cap.release()
            if not args.no_gui:
                cv2.destroyAllWindows()
            mc.shutdown()
            print("No ODrives found. Exiting before enabling camera control.")
            return
        mc.arm_all()

    if args.no_gui:
        print("Headless tracking. Press Ctrl+C to stop.")
    else:
        print("Click target color. Press SPACE to track, S to stop, R to re-pick, Q to quit.")
    if args.dry_run:
        print("DRY RUN: CAN is disabled.")

    interval = 1.0 / CONTROL_HZ
    last_control = 0.0
    driving = False
    command_text = ""
    last_edge_vy = 0.0
    last_edge_time = 0.0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Camera frame read failed.")
                break

            fh, fw = frame.shape[:2]
            frame_center_x = fw // 2

            if click_pos is not None:
                x, y = click_pos
                click_pos = None
                if 0 <= x < fw and 0 <= y < fh:
                    picked_hsv, picked_rgb = sample_color(frame, x, y)
                    target_area = pick_target_area(
                        frame,
                        picked_hsv,
                        args.min_blob,
                        args.h_tol,
                        args.s_tol,
                        args.v_tol,
                    )
                    tracking = False
                    if driving:
                        stop_drive(mc, args.dry_run)
                        driving = False
                        state["driving"] = False
                    if target_area:
                        print(f"Picked RGB{picked_rgb} HSV{picked_hsv} area={target_area:.0f}")
                    else:
                        print(f"Picked RGB{picked_rgb} HSV{picked_hsv}; target area will set when tracking starts")

            if tracking and picked_hsv:
                now = time.time()
                target, mask = find_target(
                    frame,
                    picked_hsv,
                    args.min_blob,
                    args.h_tol,
                    args.s_tol,
                    args.v_tol,
                )
                if target:
                    bx = target["x"]
                    by = target["y"]
                    area = target["area"]
                    if target_area is None:
                        target_area = area
                        print(f"Target area set to {target_area:.0f}")
                    cv2.drawContours(frame, [target["contour"]], -1, (0, 255, 0), 2)
                    cv2.circle(frame, (bx, by), 6, (0, 0, 255), -1)

                    error = (bx - frame_center_x) / frame_center_x
                    if now - last_control >= interval:
                        lateral_cmd = 0.0
                        if abs(error) >= args.deadzone:
                            lateral_cmd = -1.0 if error > 0 else 1.0
                            lateral_mag = (abs(error) - args.deadzone) / (1.0 - args.deadzone)
                            lateral_cmd *= clamp(lateral_mag, 0.0, 1.0)

                        if abs(error) >= args.edge_threshold:
                            last_edge_vy = -1.0 if error > 0 else 1.0
                            last_edge_time = now

                        range_cmd = 0.0
                        area_error = 0.0
                        if target_area and target_area > 0:
                            area_error = (target_area - area) / target_area
                            if abs(area_error) >= args.area_deadzone:
                                range_mag = (abs(area_error) - args.area_deadzone) / max(0.01, args.area_gain)
                                range_cmd = (1.0 if area_error > 0 else -1.0) * clamp(range_mag, 0.0, 1.0)

                        speed = max(
                            abs(lateral_cmd) * args.max_strafe,
                            abs(range_cmd) * args.max_range_speed,
                        )
                        if speed <= 0.01:
                            if driving:
                                stop_drive(mc, args.dry_run)
                                driving = False
                                state["driving"] = False
                            command_text = f"locked err={error:+.2f} area={area:.0f}/{target_area:.0f}"
                        else:
                            drive_vector(mc, args.dry_run, range_cmd, lateral_cmd, speed)
                            driving = True
                            state["driving"] = True
                            command_text = (
                                f"xerr={error:+.2f} aerr={area_error:+.2f} "
                                f"vx={range_cmd:+.2f} vy={lateral_cmd:+.2f} spd={speed:.2f}"
                            )
                        last_control = now
                else:
                    now = time.time()
                    if (
                        last_edge_vy
                        and now - last_edge_time <= args.edge_reacquire_seconds
                        and now - last_control >= interval
                    ):
                        speed = args.max_strafe * 0.45
                        drive_vector(mc, args.dry_run, 0.0, last_edge_vy, speed)
                        driving = True
                        state["driving"] = True
                        last_control = now
                        command_text = f"EDGE LOST reacquire vy={last_edge_vy:+.2f}"
                    else:
                        if driving:
                            stop_drive(mc, args.dry_run)
                            driving = False
                            state["driving"] = False
                        command_text = "OBJECT NOT SEEN"
                    cv2.putText(frame, command_text, (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            else:
                command_text = ""

            if not args.no_gui:
                draw_overlay(frame, args.deadzone, command_text)
                cv2.imshow("Jetson Camera Control", frame)

                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord(" ") and picked_hsv:
                    if target_area is None:
                        target_area = pick_target_area(
                            frame,
                            picked_hsv,
                            args.min_blob,
                            args.h_tol,
                            args.s_tol,
                            args.v_tol,
                        )
                    if target_area:
                        print(f"Tracking started. Desired area={target_area:.0f}")
                    else:
                        print("Tracking started. Desired area will set on first detection.")
                    tracking = True
                elif key == ord("r"):
                    tracking = False
                    picked_hsv = None
                    picked_rgb = None
                    target_area = None
                    last_edge_vy = 0.0
                    last_edge_time = 0.0
                    if driving:
                        stop_drive(mc, args.dry_run)
                        driving = False
                        state["driving"] = False
                    print("Re-pick target color.")
                elif key == ord("s"):
                    tracking = False
                    last_edge_vy = 0.0
                    last_edge_time = 0.0
                    if driving:
                        stop_drive(mc, args.dry_run)
                        driving = False
                        state["driving"] = False
                    print("Stopped tracking.")

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        state["driving"] = driving
        cleanup()
        print("Done.")


if __name__ == "__main__":
    main()
