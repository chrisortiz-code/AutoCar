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
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from camera.reader import CameraReader, list_realsense_devices

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
    <button class="toggle-btn" data-feed="feed-3d" id="btn-3d">3D Scene</button>
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
    <div class="feed hidden" id="feed-3d">
      <span class="feed-label">3D Scene</span>
      <canvas id="scene3d" width="720" height="500" style="width:100%;border:1px solid #333;background:#111;"></canvas>
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
<script type="importmap">
{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"}}
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const canvas = document.getElementById('scene3d');
let active = false;
let animId = null;
let initialized = false;

let renderer, scene, camera3d, controls;
let lidarCloud, depthCloud;

const MAX_LIDAR = 8000;
const MAX_DEPTH = 10000;
const LIDAR_URL = 'LIDAR_SCAN_URL';
let fetchTimer = null;

function initScene() {
  if (initialized) return;
  initialized = true;

  renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setClearColor(0x111111);

  scene = new THREE.Scene();
  camera3d = new THREE.PerspectiveCamera(60, 720/500, 10, 50000);
  camera3d.position.set(0, 2000, 3000);
  camera3d.lookAt(0, 0, 0);

  controls = new OrbitControls(camera3d, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.12;
  controls.minDistance = 200;
  controls.maxDistance = 30000;

  // Grid on ground plane
  scene.add(new THREE.GridHelper(8000, 20, 0x333333, 0x222222));

  // Robot marker at origin
  const markerGeo = new THREE.ConeGeometry(60, 120, 8);
  markerGeo.rotateX(Math.PI / 2);
  const marker = new THREE.Mesh(markerGeo, new THREE.MeshBasicMaterial({ color: 0x00aaff }));
  marker.position.y = 30;
  scene.add(marker);

  scene.add(new THREE.AmbientLight(0xffffff, 0.6));

  function makeCloud(maxPts, ptSize) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(maxPts * 3), 3));
    geo.setAttribute('color', new THREE.Float32BufferAttribute(new Float32Array(maxPts * 3), 3));
    geo.setDrawRange(0, 0);
    const mat = new THREE.PointsMaterial({ size: ptSize, vertexColors: true, sizeAttenuation: true });
    const pts = new THREE.Points(geo, mat);
    scene.add(pts);
    return pts;
  }

  lidarCloud = makeCloud(MAX_LIDAR, 4);
  depthCloud = makeCloud(MAX_DEPTH, 2);
}

function resizeRenderer() {
  if (!renderer) return;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight || 500;
  renderer.setSize(w, h, false);
  camera3d.aspect = w / h;
  camera3d.updateProjectionMatrix();
}

function distColor(mm, maxMm) {
  const t = Math.min(1, Math.max(0, mm / maxMm));
  return [1.0 - t, 0.7 * t, 0.25 * t];
}

async function fetchData() {
  if (!active) return;
  try {
    const [lidarRes, depthRes] = await Promise.all([
      fetch(LIDAR_URL).then(r => r.json()).catch(e => { console.warn('lidar fetch:', e); return null; }),
      fetch('/depth/points').then(r => r.json()).catch(e => { console.warn('depth fetch:', e); return null; }),
    ]);

    // Update lidar cloud (flat ring at Y=0)
    if (lidarRes && lidarRes.points && lidarRes.points.length > 0) {
      const pts = lidarRes.points;
      const pos = lidarCloud.geometry.attributes.position;
      const col = lidarCloud.geometry.attributes.color;
      let maxDist = 1;
      for (const p of pts) if (p.dist_mm > maxDist) maxDist = p.dist_mm;
      const n = Math.min(pts.length, MAX_LIDAR);
      for (let i = 0; i < n; i++) {
        const p = pts[i];
        const rad = (p.angle - 90) * Math.PI / 180;
        const d = p.dist_mm;
        pos.setXYZ(i, d * Math.cos(rad), 0, d * Math.sin(rad));
        const c = distColor(d, maxDist);
        col.setXYZ(i, c[0], c[1], c[2]);
      }
      lidarCloud.geometry.setDrawRange(0, n);
      pos.needsUpdate = true;
      col.needsUpdate = true;
    }

    // Update depth cloud
    if (depthRes && depthRes.points && depthRes.points.length > 0) {
      const dpts = depthRes.points;
      const drgb = depthRes.rgb;
      const pos = depthCloud.geometry.attributes.position;
      const col = depthCloud.geometry.attributes.color;
      const n = Math.min(dpts.length, MAX_DEPTH);
      for (let i = 0; i < n; i++) {
        // Camera coords: X right, Y down, Z forward
        // Three.js:      X right, Y up,   Z forward
        pos.setXYZ(i, dpts[i][0], -dpts[i][1], dpts[i][2]);
        col.setXYZ(i, drgb[i][0] / 255, drgb[i][1] / 255, drgb[i][2] / 255);
      }
      depthCloud.geometry.setDrawRange(0, n);
      pos.needsUpdate = true;
      col.needsUpdate = true;
    }
  } catch (e) { console.warn('3D fetch error:', e); }
}

function animate() {
  if (!active) return;
  animId = requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera3d);
}

// ----- Toggle integration -----
const btn3d = document.getElementById('btn-3d');

function start3D() {
  initScene();
  // Defer resize to next frame so the canvas is visible (not display:none)
  requestAnimationFrame(() => {
    resizeRenderer();
    active = true;
    animate();
    fetchData();
    fetchTimer = setInterval(fetchData, 200);
  });
}

function stop3D() {
  active = false;
  if (animId) { cancelAnimationFrame(animId); animId = null; }
  if (fetchTimer) { clearInterval(fetchTimer); fetchTimer = null; }
}

btn3d.addEventListener('click', () => {
  // The generic handler already toggled .active and .hidden
  if (btn3d.classList.contains('active')) {
    start3D();
  } else {
    stop3D();
  }
});

window.addEventListener('resize', () => { if (active) resizeRenderer(); });
</script>
</body>
</html>"""


class DashboardServer:
    def __init__(self, cam_reader, lidar_reader, port=8090, lidar_port=8092):
        self._cam = cam_reader
        self._lidar = lidar_reader
        parent = self

        # Build HTML with correct lidar URLs (longer string first to avoid substring match)
        self._html = PAGE_HTML.replace(
            "LIDAR_SCAN_URL",
            f"http://{{host}}:{lidar_port}/scan"
        ).replace(
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
                    data = {
                        "cam_connected": cam_snap.get("connected", False),
                        "cam_error": cam_snap.get("error"),
                        "cam_fps": cam_snap.get("fps", 0),
                        "cam_frame_count": cam_snap.get("frame_count", 0),
                        "cam_width": parent._cam.width if parent._cam else 0,
                        "cam_height": parent._cam.height if parent._cam else 0,
                        "lidar_connected": parent._lidar.connected if parent._lidar else False,
                        "lidar_error": parent._lidar.error if parent._lidar else None,
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(json.dumps(data).encode())
                elif path == "/depth/points":
                    if parent._cam is None:
                        data = {"points": [], "rgb": []}
                    else:
                        data = parent._cam.get_depth_points(step=8)
                    payload = json.dumps(data).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_response(404)
                    self.end_headers()

            def _mjpeg_stream(self, feed):
                if parent._cam is None:
                    self.send_response(503)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type",
                                 "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        if feed == "rgb":
                            jpg = parent._cam.get_rgb_jpeg()
                        else:
                            jpg = parent._cam.get_depth_jpeg()
                        if jpg is None:
                            time.sleep(0.1)
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
    parser.add_argument("--no-lidar", action="store_true", help="Disable lidar")
    # Lidar is served separately via: python3 -m lidar.viewer
    parser.add_argument("--lidar-web-port", type=int, default=8092,
                        help="Port where lidar.viewer is running (default 8092)")
    # General
    parser.add_argument("--demo", action="store_true",
                        help="Synthetic camera frames (no hardware)")
    parser.add_argument("--web-port", type=int, default=8090,
                        help="HTTP server port for the dashboard")
    args = parser.parse_args()

    cam_reader = None
    lidar_proc = None

    # Start lidar in a separate process (avoids GIL contention with camera)
    if not args.no_lidar:
        lidar_cmd = [sys.executable, "-m", "lidar.viewer",
                     "--web-port", str(args.lidar_web_port)]
        if args.demo:
            lidar_cmd.append("--demo")
        lidar_proc = subprocess.Popen(lidar_cmd)
        print(f"Lidar viewer launched on port {args.lidar_web_port} (pid {lidar_proc.pid})")
    else:
        print("Lidar disabled")

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

    server = DashboardServer(cam_reader, None, port=args.web_port,
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
        if lidar_proc:
            lidar_proc.terminate()
            lidar_proc.wait(timeout=3)
        server.stop()
        print("Done.")


if __name__ == "__main__":
    main()
