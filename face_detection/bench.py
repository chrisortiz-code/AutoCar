"""
Face detection benchmark — compare backends on live camera feed.

Usage:
    python -m face_detection.bench --backend mediapipe --preview
    python -m face_detection.bench --backend mediapipe --stream --frames 0
    python -m face_detection.bench --backend yunet --model face_detection_yunet_2023mar.onnx --stream
    python -m face_detection.bench --backend yolo --model yolov8n-face.pt --preview
    python -m face_detection.bench --backend scrfd --preview

--preview: OpenCV window (local display or X11)
--stream:  MJPEG over HTTP — open http://<jetson-ip>:8090 in a browser

Prints latency stats (min/avg/p95/max) and average FPS after the run.
"""

import argparse
import platform
import threading
import time

import cv2
import numpy as np

from .base import BACKENDS, create_detector


class MJPEGServer:
    """Tiny MJPEG-over-HTTP server. One frame buffer, any number of viewers."""

    def __init__(self, port=8090):
        from http.server import HTTPServer, BaseHTTPRequestHandler

        self._frame = None
        self._lock = threading.Lock()
        self._port = port

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type",
                                 "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        with parent._lock:
                            jpg = parent._frame
                        if jpg is None:
                            time.sleep(0.01)
                            continue
                        self.wfile.write(b"--frame\r\n"
                                         b"Content-Type: image/jpeg\r\n\r\n"
                                         + jpg + b"\r\n")
                        time.sleep(0.033)  # ~30 fps cap
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass  # silence per-request logs

        self._server = HTTPServer(("0.0.0.0", port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def update(self, frame_bgr):
        _, jpg = cv2.imencode(".jpg", frame_bgr,
                              [cv2.IMWRITE_JPEG_QUALITY, 70])
        with self._lock:
            self._frame = jpg.tobytes()

    def stop(self):
        self._server.shutdown()


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
                        help="Show live preview with detections (OpenCV window)")
    parser.add_argument("--stream", action="store_true",
                        help="Serve MJPEG stream over HTTP (open in browser)")
    parser.add_argument("--stream-port", type=int, default=8090,
                        help="Port for MJPEG stream")
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

    mjpeg = None
    if args.stream:
        mjpeg = MJPEGServer(port=args.stream_port)
        print(f"MJPEG stream at http://0.0.0.0:{args.stream_port}")

    if args.preview:
        win = f"Bench: {args.backend}"
        cv2.namedWindow(win)

    show = args.preview or args.stream

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

            if show:
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

                if mjpeg:
                    mjpeg.update(frame)

                if args.preview:
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
        if mjpeg:
            mjpeg.stop()
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
