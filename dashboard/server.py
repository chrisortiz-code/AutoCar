"""
AutoCar unified sensor dashboard — RGB + Depth + Lidar.

Camera feeds served via MJPEG on port 8090.
Lidar served on its own port (8092) via its standalone ScanServer.
Dashboard HTML embeds both.

Usage:
    python -m dashboard --demo              # synthetic frames (no hardware)
    python -m dashboard                     # auto-detect both sensors
    python -m dashboard --no-camera         # lidar only
    python -m dashboard --no-lidar          # camera only
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from camera.reader import CameraReader, list_realsense_devices
from lidar.reader import LidarReader, MODEL_BAUD, MODEL_SCAN_TYPE, list_serial_ports
from lidar.viewer import ScanServer

PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>AutoCar Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a1a; color: #d4d4d4;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; padding: 16px; }
  header { display: flex; align-items: center; gap: 12px; width: min(720px, 96vw);
           margin-bottom: 10px; }
  header h1 { font-size: 14px; font-weight: 600; color: #e0e0e0; }
  .badges { margin-left: auto; display: flex; gap: 10px; }
  .badge { font-size: 11px; }
  .badge.ok { color: #5a9a5a; }
  .badge.err { color: #c87830; }

  .toggles { display: flex; gap: 10px; width: min(720px, 96vw); margin-bottom: 10px; }
  .toggle-btn { background: #2a2a2a; border: 1px solid #444; color: #aaa;
                font-size: 11px; font-weight: 600; text-transform: uppercase;
                letter-spacing: 1px; padding: 4px 12px; border-radius: 4px;
                cursor: pointer; transition: all 0.15s; user-select: none; }
  .toggle-btn.active { background: #333; border-color: #5a9a5a; color: #d4d4d4; }
  .toggle-btn:hover { border-color: #666; }

  .feeds { display: flex; flex-direction: column; gap: 10px; align-items: center;
           width: min(720px, 96vw); }
  .feed { display: flex; flex-direction: column; align-items: center; width: 100%; }
  .feed.hidden { display: none; }
  .feed-label { font-size: 12px; font-weight: 600; color: #888; margin-bottom: 4px;
                text-transform: uppercase; letter-spacing: 1px; }
  .feed img { border: 1px solid #333; background: #111; display: block;
              width: 100%; height: auto; }
  .feed iframe { border: 1px solid #333; background: #111; display: block;
                 width: 100%; aspect-ratio: 1; }

  #stats { margin-top: 8px; width: min(720px, 96vw); font-size: 12px; color: #888;
           display: flex; flex-wrap: wrap; gap: 14px 18px; padding: 6px 0;
           border-bottom: 1px solid #2a2a2a; }
  #stats .v { color: #b0b0b0; }
  #error { margin-top: 8px; width: min(720px, 96vw); font-size: 12px; color: #c87830;
           min-height: 18px; }
</style>
</head>
<body>
  <header>
    <h1>AutoCar Dashboard</h1>
    <div class="badges">
      <span id="cam-badge" class="badge err">cam: --</span>
      <span id="lidar-badge" class="badge err">lidar: --</span>
    </div>
  </header>

  <div class="toggles">
    <button class="toggle-btn active" data-feed="feed-rgb">RGB</button>
    <button class="toggle-btn active" data-feed="feed-depth">Depth</button>
    <button class="toggle-btn active" data-feed="feed-lidar">Lidar</button>
  </div>

  <div class="feeds">
    <div class="feed" id="feed-rgb">
      <span class="feed-label">RGB</span>
      <img id="rgb" src="/stream/rgb" />
    </div>
    <div class="feed" id="feed-depth">
      <span class="feed-label">Depth</span>
      <img id="depth" src="/stream/depth" />
    </div>
    <div class="feed" id="feed-lidar">
      <span class="feed-label">Lidar</span>
      <iframe id="lidar-frame" src="LIDAR_URL" frameborder="0" scrolling="no"></iframe>
    </div>
  </div>

  <div id="stats">
    <span>cam fps <span class="v" id="s-fps">--</span></span>
    <span>cam frames <span class="v" id="s-frames">0</span></span>
    <span>resolution <span class="v" id="s-res">--</span></span>
  </div>
  <div id="error"></div>
<script>
// Toggle buttons
document.querySelectorAll('.toggle-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    btn.classList.toggle('active');
    const feed = document.getElementById(btn.dataset.feed);
    const isActive = btn.classList.contains('active');
    feed.classList.toggle('hidden', !isActive);
    const img = feed.querySelector('img');
    if (img) {
      if (isActive) {
        img.src = img.dataset.stream + '?t=' + Date.now();
      } else {
        img.src = '';
      }
    }
  });
});

document.getElementById('rgb').dataset.stream = '/stream/rgb';
document.getElementById('depth').dataset.stream = '/stream/depth';

async function pollStatus() {
  try {
    const r = await fetch('/status');
    const s = await r.json();
    const sFps = document.getElementById('s-fps');
    const sFrames = document.getElementById('s-frames');
    const sRes = document.getElementById('s-res');
    const camBadge = document.getElementById('cam-badge');
    const lidarBadge = document.getElementById('lidar-badge');
    const errEl = document.getElementById('error');
    sFps.textContent = s.cam_fps || '--';
    sFrames.textContent = s.cam_frame_count;
    sRes.textContent = s.cam_width + 'x' + s.cam_height;
    if (s.cam_connected) {
      camBadge.textContent = 'cam: live';
      camBadge.className = 'badge ok';
    } else {
      camBadge.textContent = 'cam: off';
      camBadge.className = 'badge err';
    }
    if (s.lidar_connected) {
      lidarBadge.textContent = 'lidar: live';
      lidarBadge.className = 'badge ok';
    } else {
      lidarBadge.textContent = 'lidar: off';
      lidarBadge.className = 'badge err';
    }
    const errs = [s.cam_error, s.lidar_error].filter(Boolean);
    errEl.textContent = errs.join(' | ');
  } catch (e) {}
}

setInterval(pollStatus, 1000);
pollStatus();
</script>
</body>
</html>"""


class DashboardServer:
    def __init__(self, cam_reader, lidar_reader, port=8090, lidar_port=8092):
        self._cam = cam_reader
        self._lidar = lidar_reader
        parent = self

        # Build HTML with correct lidar URL
        self._html = PAGE_HTML.replace(
            "LIDAR_URL",
            f"http://{{host}}:{lidar_port}"
        ).encode()

        class Handler(BaseHTTPRequestHandler):
            def handle(self):
                try:
                    super().handle()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/":
                    # Inject the correct host into the lidar iframe URL
                    host = self.headers.get("Host", "localhost").split(":")[0]
                    html = parent._html.replace(b"{host}", host.encode())
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(html)
                elif path == "/stream/rgb":
                    self._mjpeg_stream("rgb")
                elif path == "/stream/depth":
                    self._mjpeg_stream("depth")
                elif path == "/status":
                    cam_snap = parent._cam.get_frames() if parent._cam else {}
                    lidar_snap = parent._lidar.get_scan() if parent._lidar else {}
                    data = {
                        "cam_connected": cam_snap.get("connected", False),
                        "cam_error": cam_snap.get("error"),
                        "cam_fps": cam_snap.get("fps", 0),
                        "cam_frame_count": cam_snap.get("frame_count", 0),
                        "cam_width": parent._cam.width if parent._cam else 0,
                        "cam_height": parent._cam.height if parent._cam else 0,
                        "lidar_connected": lidar_snap.get("connected", False),
                        "lidar_error": lidar_snap.get("error"),
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(json.dumps(data).encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def _mjpeg_stream(self, feed):
                self.send_response(200)
                self.send_header("Content-Type",
                                 "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        if feed == "rgb":
                            jpg = parent._cam.get_rgb_jpeg() if parent._cam else None
                        else:
                            jpg = parent._cam.get_depth_jpeg() if parent._cam else None
                        if jpg is None:
                            time.sleep(0.01)
                            continue
                        self.wfile.write(b"--frame\r\n"
                                         b"Content-Type: image/jpeg\r\n\r\n"
                                         + jpg + b"\r\n")
                        time.sleep(0.033)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        class Threaded(ThreadingMixIn, HTTPServer):
            daemon_threads = True

        self._server = Threaded(("0.0.0.0", port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self._server.shutdown()


def main():
    parser = argparse.ArgumentParser(description="AutoCar unified sensor dashboard")
    # Camera args
    parser.add_argument("--serial", default=None, help="RealSense device serial number")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--no-camera", action="store_true", help="Disable camera")
    # Lidar args
    parser.add_argument("--lidar-port", default=None, help="Lidar serial port")
    parser.add_argument("--lidar-model", default="s2", choices=sorted(MODEL_BAUD))
    parser.add_argument("--lidar-web-port", type=int, default=8092,
                        help="HTTP port for the lidar viewer (default 8092)")
    parser.add_argument("--no-lidar", action="store_true", help="Disable lidar")
    # General
    parser.add_argument("--demo", action="store_true",
                        help="Synthetic frames for both sensors (no hardware)")
    parser.add_argument("--web-port", type=int, default=8090,
                        help="HTTP server port for the dashboard")
    args = parser.parse_args()

    cam_reader = None
    lidar_reader = None
    lidar_server = None

    # Start camera
    if not args.no_camera:
        cam_reader = CameraReader(
            serial=args.serial,
            width=args.width,
            height=args.height,
            fps=args.fps,
            demo=args.demo,
        )
        cam_reader.start()
        print("Camera: " + ("demo mode" if args.demo else "starting..."))

    # Start lidar on its own port
    if not args.no_lidar:
        baud = MODEL_BAUD[args.lidar_model]
        scan_type = MODEL_SCAN_TYPE[args.lidar_model]
        serial_port = args.lidar_port
        if not args.demo and not serial_port:
            ports = list_serial_ports()
            if len(ports) == 1:
                serial_port = ports[0]
                print(f"Lidar: auto-selected port {serial_port}")
            elif len(ports) == 0:
                print("Lidar: no serial ports found, skipping")
            else:
                print(f"Lidar: multiple ports found {ports}, use --lidar-port")
        if args.demo or serial_port:
            lidar_reader = LidarReader(
                serial_port or "demo",
                baudrate=baud,
                scan_type=scan_type,
                demo=args.demo,
            )
            lidar_reader.start()
            lidar_server = ScanServer(lidar_reader, port=args.lidar_web_port)
            print("Lidar: " + ("demo mode" if args.demo else f"{serial_port} @ {baud} baud"))
            print(f"Lidar viewer at http://localhost:{args.lidar_web_port}")

    server = DashboardServer(cam_reader, lidar_reader, port=args.web_port,
                             lidar_port=args.lidar_web_port)
    print(f"Dashboard at http://localhost:{args.web_port}")
    print("Ctrl+C to stop.")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        if cam_reader:
            cam_reader.stop()
        if lidar_reader:
            lidar_reader.stop()
        if lidar_server:
            lidar_server.stop()
        server.stop()
        print("Done.")


if __name__ == "__main__":
    main()
