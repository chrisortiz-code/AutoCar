"""
AutoCar unified sensor dashboard — RGB + Depth + Lidar.

Imports from both `camera` and `lidar` packages and serves a single
web UI with all three feeds stacked vertically, each with a toggle.

Uses MJPEG streaming for camera feeds (same approach as face_detection).

Usage:
    python -m dashboard --demo              # synthetic frames (no hardware)
    python -m dashboard                     # auto-detect both sensors
    python -m dashboard --no-camera         # lidar only
    python -m dashboard --no-lidar          # camera only
    python -m dashboard --web-port 8090
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from camera.reader import CameraReader, list_realsense_devices
from lidar.reader import LidarReader, MODEL_BAUD, MODEL_SCAN_TYPE, list_serial_ports

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
  .feed canvas { border: 1px solid #333; background: #111; display: block;
                 width: 100%; height: auto; }

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
      <canvas id="lidar" width="640" height="640"></canvas>
    </div>
  </div>

  <div id="stats">
    <span>cam fps <span class="v" id="s-fps">--</span></span>
    <span>cam frames <span class="v" id="s-frames">0</span></span>
    <span>resolution <span class="v" id="s-res">--</span></span>
    <span>lidar pts <span class="v" id="s-pts">0</span></span>
    <span>scan hz <span class="v" id="s-hz">--</span></span>
  </div>
  <div id="error"></div>
<script>
const camBadge = document.getElementById('cam-badge');
const lidarBadge = document.getElementById('lidar-badge');
const sFps = document.getElementById('s-fps');
const sFrames = document.getElementById('s-frames');
const sRes = document.getElementById('s-res');
const sPts = document.getElementById('s-pts');
const sHz = document.getElementById('s-hz');
const errEl = document.getElementById('error');

// Toggle buttons — reconnect MJPEG streams when toggling
document.querySelectorAll('.toggle-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    btn.classList.toggle('active');
    const feed = document.getElementById(btn.dataset.feed);
    const isActive = btn.classList.contains('active');
    feed.classList.toggle('hidden', !isActive);
    // Reconnect or disconnect MJPEG streams
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

// Store stream URLs for reconnection
document.getElementById('rgb').dataset.stream = '/stream/rgb';
document.getElementById('depth').dataset.stream = '/stream/depth';

async function pollStatus() {
  try {
    const r = await fetch('/status');
    const s = await r.json();
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
    sPts.textContent = s.lidar_count;
    sHz.textContent = s.lidar_scan_hz ? s.lidar_scan_hz.toFixed(1) : '--';
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

// --- Lidar polar plot ---
const canvas = document.getElementById('lidar');
const ctx = canvas.getContext('2d');
const W = canvas.width, H = canvas.height;
const CX = W / 2, CY = H / 2;
const R_MAX = Math.min(W, H) * 0.46;
let rangeM = 1;

function distColor(m, maxM) {
  const t = Math.min(1, Math.max(0, m / maxM));
  const r = Math.round(255 * (1 - t));
  const g = Math.round(180 * t);
  const b = Math.round(60 * t);
  return 'rgb(' + r + ',' + g + ',' + b + ')';
}

function niceStep(maxM) {
  if (maxM <= 1) return 0.25;
  if (maxM <= 3) return 0.5;
  if (maxM <= 8) return 1;
  if (maxM <= 20) return 2;
  return 5;
}

function drawGrid(maxM) {
  ctx.fillStyle = '#111';
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = '#2a2a2a';
  ctx.lineWidth = 1;
  const step = niceStep(maxM);
  for (let m = step; m <= maxM; m += step) {
    const r = (m / maxM) * R_MAX;
    ctx.beginPath();
    ctx.arc(CX, CY, r, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.strokeStyle = '#333';
  ctx.beginPath();
  ctx.moveTo(CX, CY - R_MAX); ctx.lineTo(CX, CY + R_MAX);
  ctx.moveTo(CX - R_MAX, CY); ctx.lineTo(CX + R_MAX, CY);
  ctx.stroke();
  ctx.fillStyle = '#666';
  ctx.font = '11px sans-serif';
  ctx.fillText('0 m', CX + 6, CY - 4);
  ctx.fillText(maxM.toFixed(1) + ' m', CX + 6, CY - R_MAX + 14);
  ctx.beginPath();
  ctx.fillStyle = '#888';
  ctx.arc(CX, CY, 4, 0, Math.PI * 2);
  ctx.fill();
}

function drawScan(points) {
  if (!points.length) { drawGrid(rangeM); return; }
  let maxDist = 0;
  for (const p of points) {
    if (p.dist_mm > maxDist) maxDist = p.dist_mm;
  }
  const dataMaxM = maxDist / 1000;
  const targetM = dataMaxM * 1.1;
  rangeM = rangeM + (targetM - rangeM) * 0.3;
  if (rangeM < 0.5) rangeM = 0.5;
  drawGrid(rangeM);
  for (const p of points) {
    const m = p.dist_mm / 1000;
    if (m <= 0) continue;
    const rad = (p.angle - 90) * Math.PI / 180;
    const r = (m / rangeM) * R_MAX;
    const x = CX + r * Math.cos(rad);
    const y = CY + r * Math.sin(rad);
    ctx.fillStyle = distColor(m, rangeM);
    ctx.fillRect(x - 1.5, y - 1.5, 3, 3);
  }
}

drawGrid(rangeM);

async function pollLidar() {
  if (document.getElementById('feed-lidar').classList.contains('hidden')) return;
  try {
    const r = await fetch('/scan');
    const s = await r.json();
    drawScan(s.points || []);
  } catch (e) {}
}

setInterval(pollLidar, 100);
pollLidar();
</script>
</body>
</html>"""


class DashboardServer:
    def __init__(self, cam_reader, lidar_reader, port=8090):
        self._cam = cam_reader
        self._lidar = lidar_reader
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def handle(self):
                try:
                    super().handle()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(PAGE_HTML.encode())
                elif path == "/stream/rgb":
                    self._mjpeg_stream("rgb")
                elif path == "/stream/depth":
                    self._mjpeg_stream("depth")
                elif path == "/scan":
                    if parent._lidar:
                        snap = parent._lidar.get_scan()
                        dists = [p["dist_mm"] for p in snap["points"]]
                        data = {
                            "points": snap["points"],
                            "count": snap["count"],
                            "scan_hz": snap["scan_hz"],
                            "connected": snap["connected"],
                            "port": snap["port"],
                            "error": snap["error"],
                            "min_mm": min(dists) if dists else None,
                            "max_mm": max(dists) if dists else None,
                        }
                    else:
                        data = {"points": [], "count": 0, "scan_hz": 0,
                                "connected": False, "error": "lidar not started"}
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(json.dumps(data).encode())
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
                        "lidar_count": lidar_snap.get("count", 0),
                        "lidar_scan_hz": lidar_snap.get("scan_hz", 0),
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
                """Push MJPEG frames over a persistent connection."""
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
                        time.sleep(0.033)  # ~30 fps
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
    parser.add_argument("--no-lidar", action="store_true", help="Disable lidar")
    # General
    parser.add_argument("--demo", action="store_true",
                        help="Synthetic frames for both sensors (no hardware)")
    parser.add_argument("--web-port", type=int, default=8090,
                        help="HTTP server port for the dashboard")
    args = parser.parse_args()

    cam_reader = None
    lidar_reader = None

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

    # Start lidar
    if not args.no_lidar:
        baud = MODEL_BAUD[args.lidar_model]
        scan_type = MODEL_SCAN_TYPE[args.lidar_model]
        lidar_port = args.lidar_port
        if not args.demo and not lidar_port:
            ports = list_serial_ports()
            if len(ports) == 1:
                lidar_port = ports[0]
                print(f"Lidar: auto-selected port {lidar_port}")
            elif len(ports) == 0:
                print("Lidar: no serial ports found, skipping")
            else:
                print(f"Lidar: multiple ports found {ports}, use --lidar-port")
        if args.demo or lidar_port:
            lidar_reader = LidarReader(
                lidar_port or "demo",
                baudrate=baud,
                scan_type=scan_type,
                demo=args.demo,
            )
            lidar_reader.start()
            print("Lidar: " + ("demo mode" if args.demo else f"{lidar_port} @ {baud} baud"))

    server = DashboardServer(cam_reader, lidar_reader, port=args.web_port)
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
        server.stop()
        print("Done.")


if __name__ == "__main__":
    main()
