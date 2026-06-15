"""
YOLO obstacle detection viewer — MJPEG web UI.

Shows live camera feed with YOLO detections overlaid, plus a colour-coded
detection list and latency/FPS stats in the browser.

Usage:
    python -m obstacle_detection.viewer
    python -m obstacle_detection.viewer --model yolo11s.engine --port 8091
    python -m obstacle_detection.viewer --world "person,chair,wall,stairs,cone,dog,car"
    python -m obstacle_detection.viewer --camera 0 --width 640 --height 480
"""

import argparse
import json
import os
import platform
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import cv2
import numpy as np
# ---------------------------------------------------------------------------
# Deterministic colour palette — same class always gets the same colour
# ---------------------------------------------------------------------------
_PALETTE = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
    "#469990", "#dcbeff", "#9a6324", "#800000", "#aaffc3",
    "#808000", "#ffd8b1", "#000075", "#a9a9a9", "#00ff7f",
]


def _hex_to_bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def _class_colour(idx):
    return _PALETTE[idx % len(_PALETTE)]


def _class_bgr(idx):
    return _hex_to_bgr(_PALETTE[idx % len(_PALETTE)])


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------
PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Obstacle Viewer</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a1a; color: #d4d4d4;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; padding: 16px; }
  header { display: flex; align-items: baseline; gap: 12px; width: 640px;
           margin-bottom: 8px; }
  header h1 { font-size: 14px; font-weight: 600; color: #e0e0e0; }
  #badge { font-size: 11px; color: #777; margin-left: auto; }
  #feed { border: 1px solid #333; display: block; }
  #stats { margin-top: 8px; width: 640px; font-size: 12px; color: #888;
           display: flex; gap: 18px; padding: 6px 0;
           border-bottom: 1px solid #2a2a2a; }
  #stats .v { color: #b0b0b0; }
  #detections { margin-top: 4px; width: 640px; max-height: 260px;
                overflow-y: auto; }
  .det-row { display: flex; align-items: center; padding: 4px 0;
             border-bottom: 1px solid #222; font-size: 12px; }
  .det-row:last-child { border-bottom: none; }
  .det-cls { font-weight: 500; min-width: 100px; }
  .det-bar { height: 2px; margin: 0 12px; flex: 1; max-width: 140px;
             background: #2a2a2a; }
  .det-bar-fill { height: 100%; }
  .det-pct { color: #777; font-size: 11px; min-width: 32px; text-align: right; }
  .empty { padding: 12px 0; color: #555; font-size: 12px; }
</style>
</head>
<body>
  <header>
    <h1>Obstacle Viewer</h1>
    <span id="badge">--</span>
  </header>
  <img id="feed" src="/stream" width="640" height="480" />
  <div id="stats">
    <span>latency <span class="v" id="s-lat">--</span></span>
    <span>fps <span class="v" id="s-fps">--</span></span>
    <span>objects <span class="v" id="s-cnt">0</span></span>
  </div>
  <div id="detections"><div class="empty">waiting for detections</div></div>
<script>
const badge = document.getElementById('badge');
const sLat = document.getElementById('s-lat');
const sFps = document.getElementById('s-fps');
const sCnt = document.getElementById('s-cnt');
const detsEl = document.getElementById('detections');

setInterval(async () => {
  try {
    const r = await fetch('/status');
    const s = await r.json();
    badge.textContent = s.count > 0 ? s.count + ' detected' : 'scanning';
    sLat.textContent = s.inf_ms.toFixed(1) + 'ms';
    sFps.textContent = s.fps.toFixed(0);
    sCnt.textContent = s.count;

    if (s.detections.length === 0) {
      detsEl.innerHTML = '<div class="empty">no objects detected</div>';
      return;
    }
    let html = '';
    for (const d of s.detections) {
      const pct = (d.conf * 100).toFixed(0);
      html += '<div class="det-row">' +
        '<span class="det-cls" style="color:' + d.colour + '">' + d.cls + '</span>' +
        '<div class="det-bar"><div class="det-bar-fill" style="width:' +
        pct + '%;background:' + d.colour + '"></div></div>' +
        '<span class="det-pct">' + pct + '%</span></div>';
    }
    detsEl.innerHTML = html;
  } catch(e) {}
}, 150);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------
class ViewerServer:
    """MJPEG + status JSON server for the obstacle viewer."""

    def __init__(self, port=8090):
        self._frame_bytes = None
        self._lock = threading.Lock()
        self._inf_ms = 0.0
        self._fps = 0.0
        self._count = 0
        self._detections = []  # list of {cls, conf, colour}

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?")[0]

                if path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(PAGE_HTML.encode())

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

                elif path == "/status":
                    with parent._lock:
                        data = {
                            "inf_ms": parent._inf_ms,
                            "fps": parent._fps,
                            "count": parent._count,
                            "detections": list(parent._detections),
                        }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(data).encode())

                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        class Threaded(ThreadingMixIn, HTTPServer):
            daemon_threads = True

        self._server = Threaded(("0.0.0.0", port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def update_frame(self, frame_bgr):
        _, jpg = cv2.imencode(".jpg", frame_bgr,
                              [cv2.IMWRITE_JPEG_QUALITY, 70])
        with self._lock:
            self._frame_bytes = jpg.tobytes()

    def set_metrics(self, *, inf_ms, fps, detections):
        with self._lock:
            self._inf_ms = inf_ms
            self._fps = fps
            self._count = len(detections)
            self._detections = detections

    def stop(self):
        self._server.shutdown()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="YOLO 11 obstacle detection viewer (MJPEG web UI)")
    parser.add_argument("--model", default=None,
                        help="Path to YOLO model (.engine or .pt). "
                             "Auto-detects yolo11s.engine / yolo11s.pt if omitted.")
    parser.add_argument("--world", default=None,
                        help="Comma-separated custom classes for YOLO-World "
                             "open-vocabulary detection. Overrides --model with "
                             "yolov8s-world.pt (auto-downloaded). "
                             'Example: --world "person,chair,wall,stairs,cone"')
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--port", type=int, default=8090,
                        help="MJPEG server port")
    parser.add_argument("--conf", type=float, default=0.25,
                        help="Confidence threshold")
    parser.add_argument("--imgsz", type=int, default=640,
                        help="YOLO inference size")
    args = parser.parse_args()

    # --- Resolve model path ---
    from ultralytics import YOLO

    if args.world:
        # YOLO-World: open-vocabulary detection with custom classes
        classes = [c.strip() for c in args.world.split(",") if c.strip()]
        model_path = args.model or "yolov8s-world.pt"
        print(f"YOLO-World mode: {classes}")
        print(f"Model: {model_path}")
        model = YOLO(model_path)
        model.set_classes(classes)
    else:
        model_path = args.model
        if model_path is None:
            if os.path.exists("yolo11s.engine"):
                model_path = "yolo11s.engine"
                print("Using TensorRT engine")
            else:
                model_path = "yolo11s.pt"
                print("Using PyTorch weights")
        print(f"Model: {model_path}")
        model = YOLO(model_path, task="detect")

    # Move to CUDA if not a TensorRT engine
    is_engine = (model_path or "").endswith(".engine")
    if not is_engine:
        import torch
        if torch.cuda.is_available():
            model.to("cuda")
            print("CUDA active")
        else:
            print("CUDA not available, using CPU")

    # Warmup
    print("Warming up model...")
    import torch
    if torch.cuda.is_available():
        dummy = torch.zeros(1, 3, args.height, args.width).cuda()
    else:
        dummy = torch.zeros(1, 3, args.height, args.width)
    for _ in range(3):
        model(dummy, verbose=False)
    print("Model ready.")

    # --- Camera ---
    if platform.system() == "Linux":
        cap = cv2.VideoCapture(args.camera, cv2.CAP_V4L2)
    else:
        cap = cv2.VideoCapture(args.camera)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, 30)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    if not cap.isOpened():
        print(f"Cannot open camera {args.camera}")
        return

    # --- Shared state ---
    lock = threading.Lock()
    latest_raw = None
    latest_annotated = None
    latest_dets = []
    latest_inf_ms = 0.0
    stop_flag = threading.Event()

    # --- Inference thread ---
    def inference_loop():
        nonlocal latest_annotated, latest_dets, latest_inf_ms
        while not stop_flag.is_set():
            with lock:
                frame = latest_raw.copy() if latest_raw is not None else None
            if frame is None:
                time.sleep(0.005)
                continue

            t0 = time.perf_counter()
            results = model(frame, verbose=False, imgsz=args.imgsz,
                            conf=args.conf)
            inf_ms = (time.perf_counter() - t0) * 1000

            # Draw boxes with per-class colours
            annotated = frame.copy()
            dets = []
            for r in results:
                for box in r.boxes:
                    cls_id = int(box.cls)
                    cls_name = model.names[cls_id]
                    conf = float(box.conf)
                    colour_bgr = _class_bgr(cls_id)
                    colour_hex = _class_colour(cls_id)

                    x1, y1, x2, y2 = box.xyxy[0].int().tolist()
                    cv2.rectangle(annotated, (x1, y1), (x2, y2),
                                  colour_bgr, 2)
                    label = f"{cls_name} {conf:.0%}"
                    (tw, th), _ = cv2.getTextSize(
                        label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                    cv2.rectangle(annotated, (x1, max(y1 - th - 6, 0)),
                                  (x1 + tw + 4, y1), colour_bgr, -1)
                    cv2.putText(annotated, label,
                                (x1 + 2, max(y1 - 4, th + 2)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                (255, 255, 255), 1)

                    dets.append({
                        "cls": cls_name,
                        "conf": conf,
                        "colour": colour_hex,
                    })

            # Sort by confidence descending
            dets.sort(key=lambda d: d["conf"], reverse=True)

            with lock:
                latest_annotated = annotated
                latest_dets = dets
                latest_inf_ms = inf_ms

    inf_thread = threading.Thread(target=inference_loop, daemon=True)
    inf_thread.start()

    # --- Web server ---
    server = ViewerServer(port=args.port)
    print(f"Obstacle viewer at http://0.0.0.0:{args.port}")
    print("Ctrl+C to stop.")

    frame_times = []
    try:
        while True:
            t0 = time.perf_counter()
            ret, frame = cap.read()
            if not ret:
                continue

            with lock:
                latest_raw = frame.copy()

            with lock:
                annotated = (latest_annotated.copy()
                             if latest_annotated is not None
                             else frame.copy())
                dets = list(latest_dets)
                inf_ms = latest_inf_ms

            server.update_frame(annotated)

            frame_times.append(time.perf_counter() - t0)
            if len(frame_times) > 30:
                frame_times.pop(0)
            fps = (1.0 / (sum(frame_times) / len(frame_times))
                   if frame_times else 0)

            server.set_metrics(inf_ms=inf_ms, fps=fps, detections=dets)

            # Don't spin faster than ~30 fps
            elapsed = time.perf_counter() - t0
            if elapsed < 0.033:
                time.sleep(0.033 - elapsed)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop_flag.set()
        cap.release()
        server.stop()
        print("Done.")


if __name__ == "__main__":
    main()
