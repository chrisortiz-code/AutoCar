"""
Object tracking benchmark — compare matching backends on live camera feed.

Flow:
  1. Shows live feed
  2. Click and drag to crop a reference image from the feed
  3. Backend tries to find that object each frame
  4. Prints latency stats

Usage:
    python -m object_tracking.bench --backend orb --stream
    python -m object_tracking.bench --backend sift --preview
    python -m object_tracking.bench --backend template --stream
    python -m object_tracking.bench --backend akaze --ref target.png --stream

--ref: skip crop step, load reference image from file
--stream: MJPEG web UI at http://<ip>:8090
--preview: OpenCV window
"""

import argparse
import platform
import threading
import time
import json

import cv2
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

from .base import BACKENDS, create_matcher


BENCH_HTML = """<!DOCTYPE html>
<html>
<head>
<title>Object Tracking Bench</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #111; color: #eee; font-family: monospace;
         display: flex; flex-direction: column; align-items: center; }
  #feed { cursor: crosshair; border: 2px solid #333; margin-top: 10px;
          user-select: none; -webkit-user-select: none; }
  #feed.tracking { border-color: #0f0; }
  #ref-container { margin-top: 8px; display: flex; align-items: center; gap: 12px; }
  #ref-img { border: 2px solid #555; display: none; }
  #status { margin-top: 8px; font-size: 16px; min-height: 24px; }
  #help { margin-top: 6px; font-size: 13px; color: #666; }
  .val { color: #0f0; }
  .warn { color: #f80; }
</style>
</head>
<body>
  <img id="feed" src="/stream" width="640" height="480" />
  <div id="ref-container">
    <span>Reference:</span>
    <img id="ref-img" height="80" />
    <span id="ref-status">Drag on feed to select target</span>
  </div>
  <div id="status">Waiting...</div>
  <div id="help">Click+drag to select &bull; R to reset &bull; Upload: drag image onto page</div>
  <canvas id="crop-canvas" style="display:none"></canvas>
<script>
const feed = document.getElementById('feed');
const refImg = document.getElementById('ref-img');
const refStatus = document.getElementById('ref-status');
const status = document.getElementById('status');
let startX = 0, startY = 0, dragging = false;

feed.addEventListener('mousedown', (e) => {
  const rect = feed.getBoundingClientRect();
  startX = (e.clientX - rect.left) / rect.width;
  startY = (e.clientY - rect.top) / rect.height;
  dragging = true;
});

feed.addEventListener('mouseup', (e) => {
  if (!dragging) return;
  dragging = false;
  const rect = feed.getBoundingClientRect();
  const endX = (e.clientX - rect.left) / rect.width;
  const endY = (e.clientY - rect.top) / rect.height;
  const x1 = Math.min(startX, endX), y1 = Math.min(startY, endY);
  const x2 = Math.max(startX, endX), y2 = Math.max(startY, endY);
  if (x2 - x1 < 0.02 || y2 - y1 < 0.02) return;
  fetch('/crop?x1='+x1.toFixed(4)+'&y1='+y1.toFixed(4)+
        '&x2='+x2.toFixed(4)+'&y2='+y2.toFixed(4));
  refStatus.textContent = 'Setting reference...';
});

document.addEventListener('keydown', (e) => {
  if (e.code === 'KeyR') fetch('/reset');
});

// drag-and-drop image file
document.body.addEventListener('dragover', (e) => e.preventDefault());
document.body.addEventListener('drop', (e) => {
  e.preventDefault();
  const file = e.dataTransfer.files[0];
  if (!file || !file.type.startsWith('image/')) return;
  const form = new FormData();
  form.append('image', file);
  fetch('/upload', {method:'POST', body: form});
  refStatus.textContent = 'Uploading...';
});

setInterval(async () => {
  try {
    const r = await fetch('/status');
    const s = await r.json();
    if (s.has_ref) {
      refImg.src = '/ref_image?' + Date.now();
      refImg.style.display = 'inline';
      refStatus.textContent = '';
      feed.className = 'tracking';
    } else {
      refImg.style.display = 'none';
      refStatus.textContent = 'Drag on feed to select target';
      feed.className = '';
    }
    if (s.matched) {
      status.innerHTML = s.backend + ' | <span class="val">' +
        s.latency_ms.toFixed(1) + 'ms</span> | conf=' +
        s.confidence.toFixed(2) + ' | angle=' + s.angle.toFixed(0) +
        '&deg; | ' + s.fps.toFixed(0) + ' fps';
    } else if (s.has_ref) {
      status.innerHTML = s.backend + ' | ' + s.latency_ms.toFixed(1) +
        'ms | <span class="warn">no match</span>';
    } else {
      status.textContent = 'Select a target object';
    }
  } catch(e) {}
}, 150);
</script>
</body>
</html>"""


class BenchServer:
    """Web server for object tracking benchmark."""

    def __init__(self, port=8090):
        self._frame_bytes = None
        self._ref_bytes = None
        self._lock = threading.Lock()
        self._crop = None       # (x1, y1, x2, y2) normalized
        self._upload = None     # raw image bytes
        self._reset = False
        self._has_ref = False
        self._matched = False
        self._latency_ms = 0.0
        self._confidence = 0.0
        self._angle = 0.0
        self._fps = 0.0
        self._backend = ""

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(BENCH_HTML.encode())

                elif path == "/stream":
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    try:
                        while True:
                            with parent._lock:
                                jpg = parent._frame_bytes
                            if jpg is None:
                                time.sleep(0.01)
                                continue
                            self.wfile.write(b"--frame\r\n"
                                             b"Content-Type: image/jpeg\r\n\r\n"
                                             + jpg + b"\r\n")
                            time.sleep(0.033)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

                elif path == "/crop":
                    qs = parse_qs(parsed.query)
                    x1 = float(qs.get("x1", [0])[0])
                    y1 = float(qs.get("y1", [0])[0])
                    x2 = float(qs.get("x2", [1])[0])
                    y2 = float(qs.get("y2", [1])[0])
                    with parent._lock:
                        parent._crop = (x1, y1, x2, y2)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")

                elif path == "/reset":
                    with parent._lock:
                        parent._reset = True
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")

                elif path == "/ref_image":
                    with parent._lock:
                        jpg = parent._ref_bytes
                    if jpg:
                        self.send_response(200)
                        self.send_header("Content-Type", "image/jpeg")
                        self.end_headers()
                        self.wfile.write(jpg)
                    else:
                        self.send_response(404)
                        self.end_headers()

                elif path == "/status":
                    with parent._lock:
                        data = {
                            "has_ref": parent._has_ref,
                            "matched": parent._matched,
                            "latency_ms": parent._latency_ms,
                            "confidence": parent._confidence,
                            "angle": parent._angle,
                            "fps": parent._fps,
                            "backend": parent._backend,
                        }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(data).encode())

                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path == "/upload":
                    content_length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(content_length)
                    # Extract image from multipart form data
                    boundary = self.headers.get("Content-Type", "").split("boundary=")[-1]
                    if boundary:
                        parts = body.split(b"--" + boundary.encode())
                        for part in parts:
                            if b"image" in part and b"\r\n\r\n" in part:
                                img_data = part.split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n--")
                                with parent._lock:
                                    parent._upload = img_data
                                break
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        class ThreadedServer(ThreadingMixIn, HTTPServer):
            daemon_threads = True

        self._server = ThreadedServer(("0.0.0.0", port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def update_frame(self, frame_bgr):
        _, jpg = cv2.imencode(".jpg", frame_bgr,
                              [cv2.IMWRITE_JPEG_QUALITY, 70])
        with self._lock:
            self._frame_bytes = jpg.tobytes()

    def set_ref_image(self, ref_bgr):
        _, jpg = cv2.imencode(".jpg", ref_bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
        with self._lock:
            self._ref_bytes = jpg.tobytes()
            self._has_ref = True

    def set_metrics(self, *, matched=False, latency_ms=0, confidence=0,
                    angle=0, fps=0, backend=""):
        with self._lock:
            self._matched = matched
            self._latency_ms = latency_ms
            self._confidence = confidence
            self._angle = angle
            self._fps = fps
            self._backend = backend

    def poll_crop(self):
        with self._lock:
            c = self._crop
            self._crop = None
        return c

    def poll_upload(self):
        with self._lock:
            u = self._upload
            self._upload = None
        return u

    def poll_reset(self):
        with self._lock:
            r = self._reset
            self._reset = False
        return r

    def clear_ref(self):
        with self._lock:
            self._has_ref = False
            self._ref_bytes = None
            self._matched = False

    def stop(self):
        self._server.shutdown()


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark object matching backends on live camera")
    parser.add_argument("--backend", required=True, choices=BACKENDS.keys())
    parser.add_argument("--ref", default=None,
                        help="Path to reference image (skip crop step)")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--frames", type=int, default=0,
                        help="Frames to benchmark (0 = unlimited)")
    parser.add_argument("--preview", action="store_true",
                        help="OpenCV window")
    parser.add_argument("--stream", action="store_true",
                        help="Web UI with MJPEG stream")
    parser.add_argument("--stream-port", type=int, default=8090)
    args = parser.parse_args()

    print(f"Loading backend: {args.backend}")
    matcher = create_matcher(args.backend)
    print(f"Backend ready: {matcher.name}")

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

    server = None
    if args.stream:
        server = BenchServer(port=args.stream_port)
        print(f"Web UI at http://0.0.0.0:{args.stream_port}")

    if args.preview:
        cv2.namedWindow("Object Tracking Bench")

    # Load reference from file if provided
    has_ref = False
    if args.ref:
        ref_img = cv2.imread(args.ref)
        if ref_img is not None:
            matcher.set_reference(ref_img)
            has_ref = True
            if server:
                server.set_ref_image(ref_img)
            print(f"Reference loaded from {args.ref}: {ref_img.shape[1]}x{ref_img.shape[0]}")
        else:
            print(f"Could not load reference image: {args.ref}")

    latencies = []
    count = 0
    print("Running..." if has_ref else "Select a target object to begin.")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            fh, fw = frame.shape[:2]

            # Handle crop from web UI
            if server:
                crop = server.poll_crop()
                if crop:
                    x1, y1, x2, y2 = crop
                    px1 = max(0, int(x1 * fw))
                    py1 = max(0, int(y1 * fh))
                    px2 = min(fw, int(x2 * fw))
                    py2 = min(fh, int(y2 * fh))
                    ref_img = frame[py1:py2, px1:px2].copy()
                    if ref_img.size > 0:
                        matcher.set_reference(ref_img)
                        has_ref = True
                        server.set_ref_image(ref_img)
                        print(f"Reference set from crop: {ref_img.shape[1]}x{ref_img.shape[0]}")

                upload = server.poll_upload()
                if upload:
                    arr = np.frombuffer(upload, dtype=np.uint8)
                    ref_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if ref_img is not None:
                        matcher.set_reference(ref_img)
                        has_ref = True
                        server.set_ref_image(ref_img)
                        print(f"Reference uploaded: {ref_img.shape[1]}x{ref_img.shape[0]}")

                if server.poll_reset():
                    has_ref = False
                    latencies.clear()
                    server.clear_ref()
                    print("Reset.")

            # Run matching
            match = None
            dt = 0
            if has_ref:
                t0 = time.perf_counter()
                match = matcher.find(frame)
                dt = (time.perf_counter() - t0) * 1000
                latencies.append(dt)
                count += 1

            # Draw results
            display = frame.copy()
            if match:
                x1 = int((match.cx - match.w / 2) * fw)
                y1 = int((match.cy - match.h / 2) * fh)
                x2 = int((match.cx + match.w / 2) * fw)
                y2 = int((match.cy + match.h / 2) * fh)
                cv2.rectangle(display, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cx, cy = int(match.cx * fw), int(match.cy * fh)
                cv2.circle(display, (cx, cy), 5, (0, 255, 0), -1)
                info = f"{matcher.name} conf={match.confidence:.2f} rot={match.angle:.0f}deg {dt:.1f}ms"
                cv2.putText(display, info, (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
            elif has_ref:
                cv2.putText(display, f"{matcher.name} no match {dt:.1f}ms", (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
            else:
                cv2.putText(display, "Select target (drag on feed)", (8, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)

            # Update outputs
            fps = 1000.0 / dt if dt > 0 else 0
            if server:
                server.update_frame(display)
                server.set_metrics(
                    matched=match is not None,
                    latency_ms=dt,
                    confidence=match.confidence if match else 0,
                    angle=match.angle if match else 0,
                    fps=fps,
                    backend=matcher.name,
                )

            if args.preview:
                cv2.imshow("Object Tracking Bench", display)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break

            if not args.preview and not args.stream and count % 50 == 0 and count > 0:
                avg = np.mean(latencies[-50:])
                print(f"  frame {count}: {dt:.1f}ms (avg {avg:.1f}ms) "
                      f"match={'yes' if match else 'no'}")

            if args.frames and count >= args.frames:
                break

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        cap.release()
        if args.preview:
            cv2.destroyAllWindows()
        if server:
            server.stop()
        matcher.close()

    if latencies:
        arr = np.array(latencies)
        matched = sum(1 for _ in latencies)  # approximate
        print(f"\n{'=' * 50}")
        print(f"Backend:  {matcher.name}")
        print(f"Frames:   {len(arr)}")
        print(f"Latency:  min={arr.min():.1f}ms  "
              f"avg={arr.mean():.1f}ms  "
              f"p95={np.percentile(arr, 95):.1f}ms  "
              f"max={arr.max():.1f}ms")
        print(f"FPS:      {1000.0 / arr.mean():.1f}")
        print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
