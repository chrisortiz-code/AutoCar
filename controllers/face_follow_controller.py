"""
Face-tracking controller — rotates the robot to keep a detected face centered.

Uses MediaPipe Face Detection for lightweight, real-time face tracking.
Sends rotation commands via the same UDP protocol as cam_jetson_controller.py
(requires universal_receiver.py to be running).

Usage:
    python face_follow_controller.py
    python face_follow_controller.py --camera 1 --preview
    python face_follow_controller.py --max-rot 2.0 --deadzone 0.15
"""

import argparse
import atexit
import os
import platform
import signal
import socket
import struct
import time

import cv2
import mediapipe as mp
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

DEADZONE = 0.10
MAX_ROT_SPEED = 3.0
CONTROL_HZ = 20
LOST_TIMEOUT = 1.0
DEFAULT_UDP_HOST = os.getenv("ROBOT_IP", "127.0.0.1")
DEFAULT_UDP_PORT = int(os.getenv("UDP_PORT", "5555"))


def parse_args():
    parser = argparse.ArgumentParser(description="Face-tracking rotation controller")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    parser.add_argument("--host", default=DEFAULT_UDP_HOST, help="UDP receiver host")
    parser.add_argument("--port", type=int, default=DEFAULT_UDP_PORT, help="UDP receiver port")
    parser.add_argument("--max-rot", type=float, default=MAX_ROT_SPEED, help="Max rotation speed in turns/s")
    parser.add_argument("--deadzone", type=float, default=DEADZONE, help="Center deadzone as fraction of half-width")
    parser.add_argument("--preview", action="store_true", help="Show OpenCV window with detection overlay")
    return parser.parse_args()


def clamp(value, low, high):
    return max(low, min(high, value))


def main():
    args = parse_args()
    state = {"cap": None, "udp_sock": None, "driving": False, "cleaned": False}
    udp_dest = (args.host, args.port)

    def cleanup():
        if state["cleaned"]:
            return
        state["cleaned"] = True
        if state["driving"] and state["udp_sock"]:
            state["udp_sock"].sendto(b"S", udp_dest)
        if state["cap"]:
            state["cap"].release()
        if args.preview:
            cv2.destroyAllWindows()
        if state["udp_sock"]:
            state["udp_sock"].close()

    def handle_signal(signum, frame):
        cleanup()
        raise KeyboardInterrupt

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if platform.system() == "Linux":
        cap = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
    else:
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Cannot open camera index {args.camera}")
        return
    state["cap"] = cap

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    state["udp_sock"] = udp_sock
    print(f"UDP control -> {args.host}:{args.port}")

    mp_face = mp.solutions.face_detection
    face_detection = mp_face.FaceDetection(model_selection=0, min_detection_confidence=0.5)

    if args.preview:
        cv2.namedWindow("Face Tracking")

    print("Face tracking active. Press Q/ESC to quit." if args.preview else "Face tracking active. Ctrl+C to stop.")

    interval = 1.0 / CONTROL_HZ
    last_control = 0.0
    last_seen = 0.0
    driving = False

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Camera frame read failed.")
                break

            fh, fw = frame.shape[:2]
            now = time.time()

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = face_detection.process(rgb_frame)

            face_center_x = None
            best_box = None

            if results.detections:
                largest_area = 0
                for detection in results.detections:
                    bbox = detection.location_data.relative_bounding_box
                    area = bbox.width * bbox.height
                    if area > largest_area:
                        largest_area = area
                        face_center_x = (bbox.xmin + bbox.width / 2)
                        best_box = bbox

            if now - last_control >= interval:
                if face_center_x is not None:
                    last_seen = now
                    error = (face_center_x - 0.5) / 0.5  # -1..+1

                    if abs(error) < args.deadzone:
                        if driving:
                            udp_sock.sendto(b"S", udp_dest)
                            driving = False
                            state["driving"] = False
                    else:
                        rot_magnitude = (abs(error) - args.deadzone) / (1.0 - args.deadzone)
                        rot_speed = (1.0 if error > 0 else -1.0) * clamp(rot_magnitude, 0.0, 1.0) * args.max_rot
                        pkt = b"D" + struct.pack("<ffff", 0.0, 0.0, 0.0, rot_speed)
                        udp_sock.sendto(pkt, udp_dest)
                        driving = True
                        state["driving"] = True
                else:
                    if driving and (now - last_seen) >= LOST_TIMEOUT:
                        udp_sock.sendto(b"S", udp_dest)
                        driving = False
                        state["driving"] = False

                last_control = now

            if args.preview:
                if best_box is not None:
                    x1 = int(best_box.xmin * fw)
                    y1 = int(best_box.ymin * fh)
                    x2 = int((best_box.xmin + best_box.width) * fw)
                    y2 = int((best_box.ymin + best_box.height) * fh)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cx = int(face_center_x * fw)
                    cy = (y1 + y2) // 2
                    cv2.circle(frame, (cx, cy), 5, (0, 255, 0), -1)

                center_x = fw // 2
                dz_px = int(fw * args.deadzone / 2)
                cv2.line(frame, (center_x, 0), (center_x, fh), (50, 50, 50), 1)
                cv2.line(frame, (center_x - dz_px, 0), (center_x - dz_px, fh), (50, 50, 50), 1)
                cv2.line(frame, (center_x + dz_px, 0), (center_x + dz_px, fh), (50, 50, 50), 1)

                if face_center_x is not None:
                    error = (face_center_x - 0.5) / 0.5
                    status = f"err={error:+.2f}"
                    if abs(error) < args.deadzone:
                        status += " CENTERED"
                    color = (0, 255, 0)
                elif driving:
                    status = "FACE LOST (coasting)"
                    color = (0, 165, 255)
                else:
                    status = "NO FACE"
                    color = (0, 0, 255)
                cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

                cv2.imshow("Face Tracking", frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        state["driving"] = driving
        cleanup()
        print("Done.")


if __name__ == "__main__":
    main()
