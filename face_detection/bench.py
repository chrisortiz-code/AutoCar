"""
Face detection benchmark — compare backends on live camera feed.

Usage:
    python -m face_detection.bench --backend mediapipe --preview
    python -m face_detection.bench --backend yunet --model face_detection_yunet_2023mar.onnx --preview
    python -m face_detection.bench --backend yolo --model yolov8n-face.pt --preview
    python -m face_detection.bench --backend scrfd --preview
    python -m face_detection.bench --backend mediapipe --frames 300

Prints latency stats (min/avg/p95/max) and average FPS after the run.
"""

import argparse
import platform
import time

import cv2
import numpy as np

from .base import BACKENDS, create_detector


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark face detection backends on live camera")
    parser.add_argument("--backend", required=True, choices=BACKENDS.keys(),
                        help="Detection backend to test")
    parser.add_argument("--model", default=None,
                        help="Path to model file (backend-specific)")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--width", type=int, default=640,
                        help="Camera capture width")
    parser.add_argument("--height", type=int, default=480,
                        help="Camera capture height")
    parser.add_argument("--frames", type=int, default=200,
                        help="Number of frames to benchmark (0 = unlimited)")
    parser.add_argument("--preview", action="store_true",
                        help="Show live preview with detections")
    args = parser.parse_args()

    # Build kwargs for the detector
    det_kwargs = {}
    if args.model:
        det_kwargs["model_path"] = args.model

    print(f"Loading backend: {args.backend}")
    detector = create_detector(args.backend, **det_kwargs)
    print(f"Backend ready: {detector.name}")

    if platform.system() == "Linux":
        cap = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
    else:
        cap = cv2.VideoCapture(args.camera)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        print(f"Cannot open camera {args.camera}")
        return

    if args.preview:
        win = f"Bench: {args.backend}"
        cv2.namedWindow(win)

    latencies = []
    count = 0
    print(f"Benchmarking {args.frames if args.frames else 'unlimited'} frames...")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            t0 = time.perf_counter()
            faces = detector.detect(frame)
            dt = (time.perf_counter() - t0) * 1000  # ms
            latencies.append(dt)
            count += 1

            if args.preview:
                fh, fw = frame.shape[:2]
                for f in faces:
                    x1 = int((f.cx - f.w / 2) * fw)
                    y1 = int((f.cy - f.h / 2) * fh)
                    x2 = int((f.cx + f.w / 2) * fw)
                    y2 = int((f.cy + f.h / 2) * fh)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(frame, f"{f.confidence:.0%}",
                                (x1, max(y1 - 6, 14)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

                fps = 1000.0 / dt if dt > 0 else 0
                avg_ms = np.mean(latencies[-30:])
                info = (f"{args.backend} | {dt:.1f}ms | "
                        f"avg {avg_ms:.1f}ms | {fps:.0f}fps | "
                        f"{len(faces)} face(s) | frame {count}")
                cv2.putText(frame, info, (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)

                cv2.imshow(win, frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
            else:
                if count % 50 == 0:
                    avg = np.mean(latencies[-50:])
                    print(f"  frame {count}: {dt:.1f}ms (avg {avg:.1f}ms) "
                          f"faces={len(faces)}")

            if args.frames and count >= args.frames:
                break

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        cap.release()
        if args.preview:
            cv2.destroyAllWindows()
        detector.close()

    if latencies:
        arr = np.array(latencies)
        print(f"\n{'=' * 50}")
        print(f"Backend:  {args.backend}")
        print(f"Frames:   {len(arr)}")
        print(f"Latency:  min={arr.min():.1f}ms  "
              f"avg={arr.mean():.1f}ms  "
              f"p95={np.percentile(arr, 95):.1f}ms  "
              f"max={arr.max():.1f}ms")
        print(f"FPS:      {1000.0 / arr.mean():.1f}")
        print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
