"""
Lightweight web UI for face tracking controller.

Serves MJPEG video + a control page with click-to-freeze and space-to-confirm.
No extra dependencies — just stdlib http.server + threading.
"""

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

import cv2

PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Face Tracker</title>
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
  #feed { cursor: crosshair; border: 1px solid #333; }
  #feed.frozen { border-color: #c87830; }
  #feed.tracking { border-color: #5a9a5a; }
  #status { margin-top: 8px; width: 640px; font-size: 12px; color: #999;
            padding: 8px 0; border-bottom: 1px solid #2a2a2a; min-height: 28px; }
  .val { color: #b0b0b0; font-weight: 500; }
  .warn { color: #c87830; font-weight: 500; }
  #help { margin-top: 6px; width: 640px; font-size: 11px; color: #555;
          text-align: center; }
</style>
</head>
<body>
  <header>
    <h1>Face Tracker</h1>
    <span id="badge">idle</span>
  </header>
  <img id="feed" src="/stream" width="640" height="480" />
  <div id="status">Click a face to select</div>
  <div id="help">click &middot; select face | space &middot; confirm | r &middot; reset | s &middot; stop</div>
<script>
const feed = document.getElementById('feed');
const status = document.getElementById('status');
const badge = document.getElementById('badge');

feed.addEventListener('click', (e) => {
  const rect = feed.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const y = (e.clientY - rect.top) / rect.height;
  fetch('/click?x=' + x.toFixed(4) + '&y=' + y.toFixed(4));
});

document.addEventListener('keydown', (e) => {
  if (e.code === 'Space') { e.preventDefault(); fetch('/confirm'); }
  if (e.code === 'KeyR') { fetch('/reset'); }
  if (e.code === 'KeyS') { fetch('/stop'); }
});

setInterval(async () => {
  try {
    const r = await fetch('/status');
    const s = await r.json();
    feed.className = s.state === 'frozen' ? 'frozen' : s.state === 'tracking' ? 'tracking' : '';
    badge.textContent = s.state;
    if (s.state === 'idle') {
      status.innerHTML = 'Click a face to select';
    } else if (s.state === 'frozen') {
      status.innerHTML = 'Selected area: <span class="warn">' + s.selected_area.toFixed(4) +
        '</span> — press space to start tracking';
    } else if (s.state === 'tracking') {
      let txt = 'area <span class="val">' + s.current_area.toFixed(4) +
        '</span> / target ' + s.target_area.toFixed(4);
      if (s.rot_speed !== undefined) txt += ' · rot ' + s.rot_speed.toFixed(2);
      if (s.vx !== undefined) txt += ' · vx ' + s.vx.toFixed(2);
      status.innerHTML = txt;
    } else if (s.state === 'lost') {
      status.innerHTML = '<span class="warn">face lost</span>';
    }
  } catch(e) {}
}, 150);
</script>
</body>
</html>"""


class WebUI:
    """MJPEG + control web server for face tracking."""

    def __init__(self, port=8090):
        self._frame_bytes = None
        self._lock = threading.Lock()
        self._port = port
        self._state = "idle"  # idle, frozen, tracking, lost
        self._click = None  # (x, y) normalized, set by click handler
        self._confirm = False
        self._reset = False
        self._stop = False
        self._selected_area = 0.0
        self._current_area = 0.0
        self._target_area = 0.0
        self._rot_speed = 0.0
        self._vx = 0.0

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path

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

                elif path == "/click":
                    qs = parse_qs(parsed.query)
                    x = float(qs.get("x", [0.5])[0])
                    y = float(qs.get("y", [0.5])[0])
                    with parent._lock:
                        parent._click = (x, y)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"ok")

                elif path == "/confirm":
                    with parent._lock:
                        parent._confirm = True
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"ok")

                elif path == "/reset":
                    with parent._lock:
                        parent._reset = True
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"ok")

                elif path == "/stop":
                    with parent._lock:
                        parent._stop = True
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"ok")

                elif path == "/status":
                    with parent._lock:
                        data = {
                            "state": parent._state,
                            "selected_area": parent._selected_area,
                            "current_area": parent._current_area,
                            "target_area": parent._target_area,
                            "rot_speed": parent._rot_speed,
                            "vx": parent._vx,
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

        class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
            daemon_threads = True

        self._server = ThreadedHTTPServer(("0.0.0.0", port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def update_frame(self, frame_bgr):
        _, jpg = cv2.imencode(".jpg", frame_bgr,
                              [cv2.IMWRITE_JPEG_QUALITY, 70])
        with self._lock:
            self._frame_bytes = jpg.tobytes()

    def set_state(self, state):
        with self._lock:
            self._state = state

    def set_metrics(self, *, current_area=None, target_area=None,
                    selected_area=None, rot_speed=None, vx=None):
        with self._lock:
            if current_area is not None:
                self._current_area = current_area
            if target_area is not None:
                self._target_area = target_area
            if selected_area is not None:
                self._selected_area = selected_area
            if rot_speed is not None:
                self._rot_speed = rot_speed
            if vx is not None:
                self._vx = vx

    def poll_click(self):
        """Returns (x, y) if clicked since last poll, else None."""
        with self._lock:
            c = self._click
            self._click = None
        return c

    def poll_confirm(self):
        with self._lock:
            c = self._confirm
            self._confirm = False
        return c

    def poll_reset(self):
        with self._lock:
            c = self._reset
            self._reset = False
        return c

    def poll_stop(self):
        with self._lock:
            c = self._stop
            self._stop = False
        return c

    def stop(self):
        self._server.shutdown()
