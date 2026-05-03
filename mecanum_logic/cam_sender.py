"""
Camera color-tracking → UDP sender (runs on PC)
Click to pick a color, then the robot strafes left/right to keep it centered.

Usage:  python cam_sender.py [JETSON_IP]
        Default IP: 192.168.2.83

Controls:
  Click      = sample color at cursor
  SPACE      = confirm color → start tracking
  R          = re-pick color
  Q / ESC    = quit
"""

import socket
import struct
import sys
import time

import cv2
import numpy as np

# ── Network ─────────────────────────────────────────────────────────
JETSON_IP   = sys.argv[1] if len(sys.argv) > 1 else "192.168.2.83"
JETSON_PORT = 5555

# ── Tuning ──────────────────────────────────────────────────────────
HSV_TOL_H    = 15      # hue tolerance (0-180 in OpenCV)
HSV_TOL_S    = 50      # saturation tolerance
HSV_TOL_V    = 50      # value tolerance
DEADZONE     = 0.10    # center 10% of frame = no movement
MAX_STRAFE   = 3.0     # turns/s at full offset
MIN_BLOB     = 500     # minimum contour area (pixels) to count as detected
SEND_HZ      = 20      # UDP send rate
SAMPLE_SIZE  = 5       # pixel region to average when clicking (5×5)

# ── State ───────────────────────────────────────────────────────────
picked_hsv = None       # HSV center color (H, S, V)
picked_rgb = None       # for display
tracking = False
click_pos = None


def on_mouse(event, x, y, flags, param):
    global click_pos
    if event == cv2.EVENT_LBUTTONDOWN:
        click_pos = (x, y)


def sample_color(frame_bgr, x, y):
    """Average a small region around (x, y) and return HSV + RGB."""
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


def find_blob(frame_bgr, hsv_center):
    """Find the largest blob matching the picked color. Returns (cx, area) or None."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv_center

    lower = np.array([max(0, h - HSV_TOL_H), max(0, s - HSV_TOL_S), max(0, v - HSV_TOL_V)])
    upper = np.array([min(179, h + HSV_TOL_H), min(255, s + HSV_TOL_S), min(255, v + HSV_TOL_V)])

    # handle hue wrapping (e.g. red spans 0 and 170+)
    if h - HSV_TOL_H < 0:
        mask1 = cv2.inRange(hsv, np.array([0, lower[1], lower[2]]), upper)
        mask2 = cv2.inRange(hsv, np.array([180 + h - HSV_TOL_H, lower[1], lower[2]]),
                            np.array([179, upper[1], upper[2]]))
        mask = cv2.bitwise_or(mask1, mask2)
    elif h + HSV_TOL_H > 179:
        mask1 = cv2.inRange(hsv, lower, np.array([179, upper[1], upper[2]]))
        mask2 = cv2.inRange(hsv, np.array([0, lower[1], lower[2]]),
                            np.array([h + HSV_TOL_H - 180, upper[1], upper[2]]))
        mask = cv2.bitwise_or(mask1, mask2)
    else:
        mask = cv2.inRange(hsv, lower, upper)

    # clean up noise
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, mask

    biggest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(biggest)
    if area < MIN_BLOB:
        return None, mask

    M = cv2.moments(biggest)
    if M["m00"] == 0:
        return None, mask

    cx = int(M["m10"] / M["m00"])
    cy = int(M["m01"] / M["m00"])
    return (cx, cy, area, biggest), mask


def draw_overlay(frame, fw, fh):
    """Draw crosshair and status text."""
    cx = fw // 2
    # center crosshair
    cv2.line(frame, (cx, 0), (cx, fh), (50, 50, 50), 1)
    # deadzone lines
    dz_px = int(fw * DEADZONE / 2)
    cv2.line(frame, (cx - dz_px, 0), (cx - dz_px, fh), (50, 50, 50), 1)
    cv2.line(frame, (cx + dz_px, 0), (cx + dz_px, fh), (50, 50, 50), 1)

    if picked_rgb:
        # color swatch
        cv2.rectangle(frame, (10, 10), (50, 50), picked_rgb[::-1], -1)
        cv2.rectangle(frame, (10, 10), (50, 50), (255, 255, 255), 1)
        label = f"RGB({picked_rgb[0]},{picked_rgb[1]},{picked_rgb[2]})"
        cv2.putText(frame, label, (58, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    if not tracking and picked_hsv:
        cv2.putText(frame, "SPACE to start tracking | click to re-pick",
                    (10, fh - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    elif not picked_hsv:
        cv2.putText(frame, "Click a color to pick",
                    (10, fh - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    elif tracking:
        cv2.putText(frame, "TRACKING | R=re-pick  Q=quit",
                    (10, fh - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)


def main():
    global picked_hsv, picked_rgb, tracking, click_pos

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (JETSON_IP, JETSON_PORT)

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Cannot open webcam!")
        return

    cv2.namedWindow("Camera")
    cv2.setMouseCallback("Camera", on_mouse)

    print(f"Sending to {JETSON_IP}:{JETSON_PORT}")
    print("Click a color to pick, SPACE to track, Q to quit.")

    was_sending = False
    interval = 1.0 / SEND_HZ
    last_send = 0.0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            fh, fw = frame.shape[:2]
            frame_center_x = fw // 2

            # ── Handle click → pick color ──
            if click_pos is not None:
                x, y = click_pos
                click_pos = None
                if 0 <= x < fw and 0 <= y < fh:
                    picked_hsv, picked_rgb = sample_color(frame, x, y)
                    tracking = False
                    print(f"Picked RGB{picked_rgb}  HSV{picked_hsv}")

            # ── Tracking mode ──
            if tracking and picked_hsv:
                now = time.time()
                result, mask = find_blob(frame, picked_hsv)

                if result:
                    bx, by, area, contour = result
                    # draw blob outline + center
                    cv2.drawContours(frame, [contour], -1, (0, 255, 0), 2)
                    cv2.circle(frame, (bx, by), 6, (0, 0, 255), -1)

                    # error: -1 (target is left) to +1 (target is right)
                    error = (bx - frame_center_x) / frame_center_x

                    # draw error bar
                    bar_x = int(fw / 2 + error * fw / 2)
                    cv2.line(frame, (bar_x, 0), (bar_x, 20), (0, 0, 255), 3)

                    # send at controlled rate
                    if now - last_send >= interval:
                        if abs(error) < DEADZONE:
                            # in deadzone → stop
                            if was_sending:
                                sock.sendto(b'S', dest)
                                was_sending = False
                        else:
                            # strafe: vy = direction, trans_speed = magnitude
                            vy = 1.0 if error > 0 else -1.0
                            speed = (abs(error) - DEADZONE) / (1.0 - DEADZONE) * MAX_STRAFE
                            pkt = b'D' + struct.pack('<ffff', 0.0, vy, speed, 0.0)
                            sock.sendto(pkt, dest)
                            was_sending = True
                        last_send = now

                    # status text
                    cv2.putText(frame, f"err={error:+.2f}  area={area}",
                                (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                else:
                    # no blob → stop
                    if now - last_send >= interval:
                        if was_sending:
                            sock.sendto(b'S', dest)
                            was_sending = False
                        last_send = now
                    cv2.putText(frame, "TARGET LOST", (10, 70),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            draw_overlay(frame, fw, fh)
            cv2.imshow("Camera", frame)

            # ── Key handling ──
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:  # Q or ESC
                if was_sending:
                    sock.sendto(b'S', dest)
                break
            elif key == ord(' ') and picked_hsv and not tracking:
                tracking = True
                print("Tracking started!")
            elif key == ord('r'):
                tracking = False
                picked_hsv = None
                picked_rgb = None
                if was_sending:
                    sock.sendto(b'S', dest)
                    was_sending = False
                print("Re-pick: click a new color.")

    except KeyboardInterrupt:
        print("\nInterrupted.")
        sock.sendto(b'S', dest)
    finally:
        cap.release()
        cv2.destroyAllWindows()
        sock.close()
        print("Done.")


if __name__ == "__main__":
    main()
