"""MJPEG streaming web UI with interactive controls for face follow controller.

Serves an MJPEG stream and a simple HTML control page. The controller polls
for clicks, confirms, resets, and stops via thread-safe queues.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import cv2

_CONTROL_PAGE = b"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Face Follow</title>
<style>
body{margin:0;background:#111;color:#eee;font-family:system-ui;display:flex;flex-direction:column;align-items:center}
img{width:100%;max-width:640px;cursor:crosshair}
#controls{padding:8px;display:flex;gap:8px;flex-wrap:wrap;justify-content:center}
button{padding:8px 16px;border:none;border-radius:4px;cursor:pointer;font-size:14px}
#confirm-btn{background:#2a2}#reset-btn{background:#a22}#stop-btn{background:#a60}
button:hover{filter:brightness(1.2)}
#status{padding:4px 12px;font-size:13px;color:#aaa}
</style></head><body>
<img id="stream" src="/stream">
<div id="controls">
<button id="confirm-btn" onclick="send('confirm')">Confirm</button>
<button id="reset-btn" onclick="send('reset')">Reset</button>
<button id="stop-btn" onclick="send('stop')">Stop</button>
</div>
<div id="status"></div>
<script>
const img=document.getElementById('stream');
img.addEventListener('click',e=>{
  const r=img.getBoundingClientRect();
  const x=(e.clientX-r.left)/r.width, y=(e.clientY-r.top)/r.height;
  fetch('/click?x='+x.toFixed(4)+'&y='+y.toFixed(4));
});
function send(action){fetch('/'+action)}
setInterval(()=>fetch('/state').then(r=>r.json()).then(d=>{
  document.getElementById('status').textContent=JSON.stringify(d);
}).catch(()=>{}),500);
</script></body></html>"""


class WebUI:
    """MJPEG stream + interactive control server for face follow."""

    def __init__(self, port: int = 8090) -> None:
        self._frame: bytes | None = None
        self._lock = threading.Lock()
        self._clicks: list[tuple[float, float]] = []
        self._confirms: list[bool] = []
        self._resets: list[bool] = []
        self._stops: list[bool] = []
        self._state = "idle"
        self._metrics: dict = {}

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/" or path == "":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(_CONTROL_PAGE)

                elif path == "/stream":
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
                            time.sleep(0.033)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

                elif path == "/click":
                    qs = parse_qs(parsed.query)
                    x = float(qs.get("x", [0.5])[0])
                    y = float(qs.get("y", [0.5])[0])
                    with parent._lock:
                        parent._clicks.append((x, y))
                    self.send_response(200)
                    self.end_headers()

                elif path == "/confirm":
                    with parent._lock:
                        parent._confirms.append(True)
                    self.send_response(200)
                    self.end_headers()

                elif path == "/reset":
                    with parent._lock:
                        parent._resets.append(True)
                    self.send_response(200)
                    self.end_headers()

                elif path == "/stop":
                    with parent._lock:
                        parent._stops.append(True)
                    self.send_response(200)
                    self.end_headers()

                elif path == "/state":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    with parent._lock:
                        data = {"state": parent._state, **parent._metrics}
                    self.wfile.write(json.dumps(data).encode())

                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("0.0.0.0", port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def update_frame(self, frame_bgr) -> None:
        _, jpg = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 70])
        with self._lock:
            self._frame = jpg.tobytes()

    def set_state(self, state: str) -> None:
        with self._lock:
            self._state = state

    def set_metrics(self, **kwargs) -> None:
        with self._lock:
            self._metrics.update(kwargs)

    def poll_click(self) -> tuple[float, float] | None:
        with self._lock:
            return self._clicks.pop(0) if self._clicks else None

    def poll_confirm(self) -> bool:
        with self._lock:
            if self._confirms:
                self._confirms.clear()
                return True
            return False

    def poll_reset(self) -> bool:
        with self._lock:
            if self._resets:
                self._resets.clear()
                return True
            return False

    def poll_stop(self) -> bool:
        with self._lock:
            if self._stops:
                self._stops.clear()
                return True
            return False

    def stop(self) -> None:
        self._server.shutdown()
