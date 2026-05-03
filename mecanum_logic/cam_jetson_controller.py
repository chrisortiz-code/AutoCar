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
finds the largest target blob, and drives sideways until that blob's centroid is
centered in the camera frame.
"""

import argparse
import platform
import time

import cv2
import numpy as np

from can_bus import MecanumCAN


# Color matching. Start loose, then tune on the real lighting.
HSV_TOL_H = 15
HSV_TOL_S = 50
HSV_TOL_V = 50

# Tracking and safety.
DEADZONE = 0.10       # center 10% of frame means stop
MAX_STRAFE = 3.0     # turns/s at full camera offset
MIN_BLOB = 500       # minimum matching contour area in pixels
CONTROL_HZ = 20
SAMPLE_SIZE = 5


picked_hsv = None
picked_rgb = None
tracking = False
click_pos = None


def parse_args():
    parser = argparse.ArgumentParser(description="Jetson local camera color controller")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--max-strafe", type=float, default=MAX_STRAFE, help="Max strafe speed in turns/s")
    parser.add_argument("--deadzone", type=float, default=DEADZONE, help="Centered deadzone as fraction of frame width")
    parser.add_argument("--min-blob", type=float, default=MIN_BLOB, help="Minimum contour area to accept target")
    parser.add_argument("--dry-run", action="store_true", help="Show camera and print commands without using CAN")
    return parser.parse_args()


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


def color_mask(frame_bgr, hsv_center):
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv_center

    lower = np.array([max(0, h - HSV_TOL_H), max(0, s - HSV_TOL_S), max(0, v - HSV_TOL_V)])
    upper = np.array([min(179, h + HSV_TOL_H), min(255, s + HSV_TOL_S), min(255, v + HSV_TOL_V)])

    if h - HSV_TOL_H < 0:
        mask1 = cv2.inRange(hsv, np.array([0, lower[1], lower[2]]), upper)
        mask2 = cv2.inRange(
            hsv,
            np.array([180 + h - HSV_TOL_H, lower[1], lower[2]]),
            np.array([179, upper[1], upper[2]]),
        )
        mask = cv2.bitwise_or(mask1, mask2)
    elif h + HSV_TOL_H > 179:
        mask1 = cv2.inRange(hsv, lower, np.array([179, upper[1], upper[2]]))
        mask2 = cv2.inRange(
            hsv,
            np.array([0, lower[1], lower[2]]),
            np.array([h + HSV_TOL_H - 180, upper[1], upper[2]]),
        )
        mask = cv2.bitwise_or(mask1, mask2)
    else:
        mask = cv2.inRange(hsv, lower, upper)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def find_target(frame_bgr, hsv_center, min_blob):
    mask = color_mask(frame_bgr, hsv_center)
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


def main():
    global picked_hsv, picked_rgb, tracking, click_pos

    args = parse_args()

    if platform.system() == "Linux":
        cap = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
    else:
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Cannot open camera index {args.camera}")
        return

    try:
        cv2.namedWindow("Jetson Camera Control")
        cv2.setMouseCallback("Jetson Camera Control", on_mouse)
    except cv2.error:
        cap.release()
        raise

    mc = None
    if not args.dry_run:
        mc = MecanumCAN(current_limit=30.0)
        mc.connect()
        mc.arm_all()

    print("Click target color. Press SPACE to track, S to stop, R to re-pick, Q to quit.")
    if args.dry_run:
        print("DRY RUN: CAN is disabled.")

    interval = 1.0 / CONTROL_HZ
    last_control = 0.0
    driving = False
    command_text = ""

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
                    tracking = False
                    if driving:
                        stop_drive(mc, args.dry_run)
                        driving = False
                    print(f"Picked RGB{picked_rgb} HSV{picked_hsv}")

            if tracking and picked_hsv:
                now = time.time()
                target, mask = find_target(frame, picked_hsv, args.min_blob)
                if target:
                    bx = target["x"]
                    by = target["y"]
                    area = target["area"]
                    cv2.drawContours(frame, [target["contour"]], -1, (0, 255, 0), 2)
                    cv2.circle(frame, (bx, by), 6, (0, 0, 255), -1)

                    error = (bx - frame_center_x) / frame_center_x
                    if now - last_control >= interval:
                        if abs(error) < args.deadzone:
                            if driving:
                                stop_drive(mc, args.dry_run)
                                driving = False
                            command_text = f"centered err={error:+.2f} area={area:.0f}"
                        else:
                            vy = 1.0 if error > 0 else -1.0
                            speed = (abs(error) - args.deadzone) / (1.0 - args.deadzone) * args.max_strafe
                            if args.dry_run:
                                print(f"DRIVE vx=0.00 vy={vy:+.2f} trans_speed={speed:.2f} rot=0.00")
                            else:
                                mc.drive(0.0, vy, speed, 0.0)
                            driving = True
                            command_text = f"err={error:+.2f} speed={speed:.2f} area={area:.0f}"
                        last_control = now
                else:
                    if driving:
                        stop_drive(mc, args.dry_run)
                        driving = False
                    command_text = "OBJECT NOT SEEN"
                    cv2.putText(frame, command_text, (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            else:
                command_text = ""

            draw_overlay(frame, args.deadzone, command_text)
            cv2.imshow("Jetson Camera Control", frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord(" ") and picked_hsv:
                tracking = True
                print("Tracking started.")
            elif key == ord("r"):
                tracking = False
                picked_hsv = None
                picked_rgb = None
                if driving:
                    stop_drive(mc, args.dry_run)
                    driving = False
                print("Re-pick target color.")
            elif key == ord("s"):
                tracking = False
                if driving:
                    stop_drive(mc, args.dry_run)
                    driving = False
                print("Stopped tracking.")

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        if driving:
            stop_drive(mc, args.dry_run)
        cap.release()
        cv2.destroyAllWindows()
        if mc:
            mc.shutdown()
        print("Done.")


if __name__ == "__main__":
    main()
