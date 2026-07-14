"""
Face-tracking controller — two modes:

  Trace (default): Rotates to keep the largest face centered. Starts
  immediately, no interaction needed.

  Follow (--follow): Trace + forward/backward to maintain distance.
  Requires selecting a target face area first — click face in web UI
  or preview, then press SPACE to confirm.

Uses the pluggable face_detection framework — any backend works.
Sends commands via the same UDP protocol as cam_jetson_controller.py
(requires universal_receiver.py to be running).

Usage:
    python face_follow_controller.py --backend mediapipe --stream
    python face_follow_controller.py --backend mediapipe --preview
    python face_follow_controller.py --follow --backend mediapipe --stream
"""

import argparse
import atexit
import os
import platform
import signal
import socket
import struct
import sys
import time

import cv2
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from face_detection import create_detector, BACKENDS

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

DEADZONE = 0.10
MAX_ROT_SPEED = 3.0
MAX_RANGE_SPEED = 5.0
AREA_DEADZONE = 0.08
AREA_GAIN = 0.70
CONTROL_HZ = 20
LOST_TIMEOUT = 1.0
DEFAULT_UDP_HOST = os.getenv("ROBOT_IP", "127.0.0.1")
DEFAULT_UDP_PORT = int(os.getenv("UDP_PORT", "5555"))


def parse_args():
    parser = argparse.ArgumentParser(description="Face-tracking controller")
    parser.add_argument("--backend", default="mediapipe", choices=BACKENDS.keys(),
                        help="Face detection backend")
    parser.add_argument("--model", default=None,
                        help="Path to model file (backend-specific)")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--host", default=DEFAULT_UDP_HOST, help="UDP receiver host")
    parser.add_argument("--port", type=int, default=DEFAULT_UDP_PORT, help="UDP receiver port")
    parser.add_argument("--max-rot", type=float, default=MAX_ROT_SPEED, help="Max rotation speed in turns/s")
    parser.add_argument("--deadzone", type=float, default=DEADZONE, help="Center deadzone as fraction of half-width")
    parser.add_argument("--follow", action="store_true",
                        help="Enable follow mode: trace + fwd/back. Click face to set target area.")
    parser.add_argument("--max-range-speed", type=float, default=MAX_RANGE_SPEED, help="Max forward/back speed in turns/s")
    parser.add_argument("--area-deadzone", type=float, default=AREA_DEADZONE, help="Accepted area error before fwd/back correction")
    parser.add_argument("--area-gain", type=float, default=AREA_GAIN, help="Area error that maps to full fwd/back command")
    parser.add_argument("--preview", action="store_true",
                        help="MJPEG preview on port 8090 (no robot control)")
    parser.add_argument("--stream", action="store_true", help="Web UI with MJPEG stream (open in browser)")
    parser.add_argument("--stream-port", type=int, default=8090, help="Port for web UI")
    return parser.parse_args()


def clamp(value, low, high):
    return max(low, min(high, value))


def draw_overlay(frame, faces, best, args, mode, target_area, rot_speed, vx):
    """Draw bounding boxes and status on frame."""
    fh, fw = frame.shape[:2]

    for f in faces:
        x1 = int((f.cx - f.w / 2) * fw)
        y1 = int((f.cy - f.h / 2) * fh)
        x2 = int((f.cx + f.w / 2) * fw)
        y2 = int((f.cy + f.h / 2) * fh)
        is_best = (best is not None and f is best)
        color = (0, 255, 0) if is_best else (100, 100, 100)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"{f.confidence:.0%}"
        if args.follow:
            label += f" a={f.area:.4f}"
        cv2.putText(frame, label, (x1, max(y1 - 6, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)

    # Deadzone lines
    center_x = fw // 2
    dz_px = int(fw * args.deadzone / 2)
    cv2.line(frame, (center_x, 0), (center_x, fh), (50, 50, 50), 1)
    cv2.line(frame, (center_x - dz_px, 0), (center_x - dz_px, fh), (50, 50, 50), 1)
    cv2.line(frame, (center_x + dz_px, 0), (center_x + dz_px, fh), (50, 50, 50), 1)

    # Status text
    if mode == "tracing" and best is not None:
        status = f"TRACING rot={rot_speed:+.2f}"
        cv2.putText(frame, status, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    elif mode == "following" and best is not None:
        area_err = (target_area - best.area) / target_area if target_area else 0
        status = f"FOLLOWING rot={rot_speed:+.2f}  area_err={area_err:+.2f}  vx={vx:+.2f}"
        cv2.putText(frame, status, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
    elif mode in ("tracing", "following"):
        cv2.putText(frame, "FACE LOST", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
    elif mode == "selecting":
        cv2.putText(frame, "Click face to select target distance", (8, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)


def find_nearest_face(faces, click_x, click_y):
    """Find the face whose center is nearest to the click point."""
    if not faces:
        return None
    return min(faces, key=lambda f: (f.cx - click_x) ** 2 + (f.cy - click_y) ** 2)


def main():
    args = parse_args()
    web = None
    state = {"cap": None, "udp_sock": None, "driving": False, "cleaned": False,
             "detector": None, "web": None}
    udp_dest = (args.host, args.port)

    def cleanup():
        if state["cleaned"]:
            return
        state["cleaned"] = True
        if state["driving"] and state["udp_sock"]:
            state["udp_sock"].sendto(b"S", udp_dest)
        if state["detector"]:
            state["detector"].close()
        if state["cap"]:
            state["cap"].release()
        if state["web"]:
            state["web"].stop()
        if state["udp_sock"]:
            state["udp_sock"].close()

    def handle_signal(signum, frame):
        cleanup()
        raise KeyboardInterrupt

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Create detector
    det_kwargs = {}
    if args.model:
        det_kwargs["model_path"] = args.model
    print(f"Loading backend: {args.backend}")
    detector = create_detector(args.backend, **det_kwargs)
    state["detector"] = detector
    print(f"Backend ready: {detector.name}")

    if platform.system() == "Linux":
        cap = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
    else:
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Cannot open camera index {args.camera}")
        return
    state["cap"] = cap

    # UDP control (disabled in preview-only mode)
    udp_sock = None
    if not args.preview or args.stream:
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        state["udp_sock"] = udp_sock
        print(f"UDP control -> {args.host}:{args.port}")
    else:
        print("Preview mode — robot control disabled")

    # Web UI — start for --stream or --preview
    if args.stream or args.preview:
        from face_detection.web_ui import WebUI
        web = WebUI(port=args.stream_port)
        state["web"] = web
        print(f"Web UI at http://0.0.0.0:{args.stream_port}")

    if args.follow:
        print("Follow mode: click face to set target distance, then SPACE to start.")
    else:
        print("Trace mode: rotating to track largest face.")
    print("Ctrl+C to stop.")

    interval = 1.0 / CONTROL_HZ
    last_control = 0.0
    last_seen = 0.0
    driving = False
    target_area = None
    frozen_frame = None
    selected_face = None
    rot_speed = 0.0
    vx = 0.0
    last_faces = []
    last_display_frame = None

    # Trace mode: start tracking immediately
    # Follow mode: wait for user to select a face
    tracking = not args.follow

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Camera frame read failed.")
                break

            fh, fw = frame.shape[:2]
            now = time.time()

            # --- Handle interactive commands (follow mode only) ---
            if args.follow:
                if web:
                    click = web.poll_click()
                    if click is not None:
                        cx, cy = click
                        selected_face = find_nearest_face(last_faces, cx, cy)
                        print(f"Click at ({cx:.2f}, {cy:.2f}), "
                              f"{len(last_faces)} faces available, "
                              f"selected={'yes' if selected_face else 'no'}")
                        if selected_face:
                            frozen_frame = (last_display_frame.copy()
                                            if last_display_frame is not None
                                            else frame.copy())
                            tracking = False
                            if driving and udp_sock:
                                udp_sock.sendto(b"S", udp_dest)
                                driving = False
                                state["driving"] = False
                            draw_overlay(frozen_frame, last_faces, selected_face,
                                         args, "selecting", None, 0.0, 0.0)
                            cv2.putText(frozen_frame,
                                        f"SELECTED area={selected_face.area:.4f} - press SPACE",
                                        (8, fh - 16), cv2.FONT_HERSHEY_SIMPLEX,
                                        0.55, (0, 165, 255), 2)
                            web.set_state("frozen")
                            web.set_metrics(selected_area=selected_face.area)
                            web.update_frame(frozen_frame)
                            print(f"Selected face area={selected_face.area:.4f}")

                    if web.poll_confirm() and selected_face is not None:
                        target_area = selected_face.area
                        tracking = True
                        frozen_frame = None
                        selected_face = None
                        web.set_state("tracking")
                        web.set_metrics(target_area=target_area)
                        print(f"Following started, target area={target_area:.4f}")

                    if web.poll_reset():
                        tracking = False
                        frozen_frame = None
                        selected_face = None
                        target_area = None
                        if driving and udp_sock:
                            udp_sock.sendto(b"S", udp_dest)
                            driving = False
                            state["driving"] = False
                        web.set_state("idle")
                        print("Reset.")

                    if web.poll_stop():
                        tracking = False
                        if driving and udp_sock:
                            udp_sock.sendto(b"S", udp_dest)
                            driving = False
                            state["driving"] = False
                        web.set_state("idle")
                        print("Stopped.")

                # Handle frozen frame in follow mode (skip detection while frozen)
                if frozen_frame is not None:
                    continue

            # --- Normal detection ---
            faces = detector.detect(frame)
            last_faces = faces
            best = max(faces, key=lambda f: f.area) if faces else None

            # --- Control loop ---
            rot_speed = 0.0
            vx = 0.0
            if tracking and now - last_control >= interval:
                if best is not None:
                    last_seen = now
                    horiz_error = (best.cx - 0.5) / 0.5

                    # Rotation (both trace and follow)
                    if abs(horiz_error) >= args.deadzone:
                        rot_magnitude = (abs(horiz_error) - args.deadzone) / (1.0 - args.deadzone)
                        rot_speed = (1.0 if horiz_error > 0 else -1.0) * clamp(rot_magnitude, 0.0, 1.0) * args.max_rot

                    # Forward/back (follow only)
                    trans_speed = 0.0
                    if args.follow and target_area:
                        area_error = (target_area - best.area) / target_area
                        if abs(area_error) >= args.area_deadzone:
                            range_mag = (abs(area_error) - args.area_deadzone) / max(0.01, args.area_gain)
                            vx = (1.0 if area_error > 0 else -1.0) * clamp(range_mag, 0.0, 1.0)
                            trans_speed = abs(vx) * args.max_range_speed

                    if abs(rot_speed) < 0.01 and abs(vx) < 0.01:
                        if driving and udp_sock:
                            udp_sock.sendto(b"S", udp_dest)
                            driving = False
                            state["driving"] = False
                    elif udp_sock:
                        pkt = b"D" + struct.pack("<ffff", vx, 0.0, trans_speed, rot_speed)
                        udp_sock.sendto(pkt, udp_dest)
                        driving = True
                        state["driving"] = True
                else:
                    if driving and udp_sock and (now - last_seen) >= LOST_TIMEOUT:
                        udp_sock.sendto(b"S", udp_dest)
                        driving = False
                        state["driving"] = False

                last_control = now

            # --- Determine display mode ---
            if not tracking and args.follow:
                mode = "selecting"
            elif tracking and args.follow and target_area:
                mode = "following"
            else:
                mode = "tracing"

            # --- Draw overlay ---
            display = frame.copy()
            draw_overlay(display, faces, best if tracking else None, args,
                         mode, target_area, rot_speed, vx)
            last_display_frame = frame.copy()

            # --- Update web UI ---
            if web:
                web.update_frame(display)
                if mode == "following" and best:
                    web.set_state("tracking")
                    web.set_metrics(current_area=best.area, target_area=target_area or 0,
                                    rot_speed=rot_speed, vx=vx)
                elif mode == "tracing" and best:
                    web.set_state("tracking")
                    web.set_metrics(rot_speed=rot_speed)
                elif tracking and not best:
                    web.set_state("lost")
                elif frozen_frame is None:
                    web.set_state("idle")

            # --- Throttle loop when no display waitKey ---
            if not web:
                time.sleep(0.001)

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        state["driving"] = driving
        cleanup()
        print("Done.")


if __name__ == "__main__":
    main()
