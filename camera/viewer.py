"""
Intel RealSense D435 live viewer — RGB + depth web UI.

Shows real-time color and depth streams from a D435 over HTTP (MJPEG).

Usage:
    python -m camera.viewer                          # auto-detect
    python -m camera.viewer --demo                   # synthetic frames
    python -m camera.viewer --web-port 8093
    python -m camera.viewer --width 1280 --height 720
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from .reader import CameraReader, list_realsense_devices

PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>D435 Camera Viewer</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a1a; color: #d4d4d4;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; padding: 16px; }
  header { display: flex; align-items: baseline; gap: 12px; width: min(1320px, 96vw);
           margin-bottom: 8px; }
  header h1 { font-size: 14px; font-weight: 600; color: #e0e0e0; }
  #badge { font-size: 11px; margin-left: auto; }
  #badge.ok { color: #5a9a5a; }
  #badge.err { color: #c87830; }
  .feeds { display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; }
  .feed { display: flex; flex-direction: column; align-items: center; }
  .feed-label { font-size: 12px; font-weight: 600; color: #888; margin-bottom: 4px;
                text-transform: uppercase; letter-spacing: 1px; }
  .feed img { border: 1px solid #333; background: #111; display: block; }
  #stats { margin-top: 8px; width: min(1320px, 96vw); font-size: 12px; color: #888;
           display: flex; flex-wrap: wrap; gap: 14px 18px; padding: 6px 0;
           border-bottom: 1px solid #2a2a2a; }
  #stats .v { color: #b0b0b0; }
  #error { margin-top: 8px; width: min(1320px, 96vw); font-size: 12px; color: #c87830;
           min-height: 18px; }
</style>
</head>
<body>
  <header>
    <h1>RealSense D435</h1>
    <span id="badge" class="err">connecting</span>
  </header>
  <div class="feeds">
    <div class="feed">
      <span class="feed-label">RGB</span>
      <img id="rgb" width="640" height="480" />
    </div>
    <div class="feed">
      <span class="feed-label">Depth</span>
      <img id="depth" width="640" height="480" />
    </div>
  </div>
  <div id="stats">
    <span>fps <span class="v" id="s-fps">--</span></span>
    <span>frames <span class="v" id="s-frames">0</span></span>
    <span>resolution <span class="v" id="s-res">--</span></span>
  </div>
  <div id="error"></div>
<script>
const badge = document.getElementById('badge');
const sFps = document.getElementById('s-fps');
const sFrames = document.getElementById('s-frames');
const sRes = document.getElementById('s-res');
const errEl = document.getElementById('error');
const rgbImg = document.getElementById('rgb');
const depthImg = document.getElementById('depth');

// MJPEG via polling — fetch individual frames to avoid stale connections
let seq = 0;
function refreshFeeds() {
  const t = Date.now();
  rgbImg.src = '/rgb.jpg?t=' + t;
  depthImg.src = '/depth.jpg?t=' + t;
}

async function pollStatus() {
  try {
    const r = await fetch('/status');
    const s = await r.json();
    sFps.textContent = s.fps || '--';
    sFrames.textContent = s.frame_count;
    sRes.textContent = s.width + 'x' + s.height;
    if (s.connected) {
      badge.textContent = 'live';
      badge.className = 'ok';
      errEl.textContent = '';
    } else {
      badge.textContent = 'disconnected';
      badge.className = 'err';
      errEl.textContent = s.error || 'waiting for camera...';
    }
  } catch (e) {}
}

setInterval(refreshFeeds, 100);  // ~10 fps display
setInterval(pollStatus, 1000);
refreshFeeds();
pollStatus();
</script>
</body>
</html>"""

# 1x1 black JPEG placeholder
_BLANK_JPEG = bytes([
    0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
    0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
    0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07, 0x09,
    0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
    0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D, 0x1A, 0x1C, 0x1C, 0x20,
    0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
    0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32,
    0x3C, 0x2E, 0x33, 0x34, 0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
    0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x1F, 0x00, 0x00,
    0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
    0x09, 0x0A, 0x0B, 0xFF, 0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01, 0x03,
    0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04, 0x00, 0x00, 0x01, 0x7D,
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
    0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08,
    0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
    0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
    0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
    0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3,
    0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6,
    0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9,
    0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2,
    0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4,
    0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01,
    0x00, 0x00, 0x3F, 0x00, 0x7B, 0x94, 0x11, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xD9,
])


class CameraServer:
    def __init__(self, reader, port=8093):
        self._reader = reader
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(PAGE_HTML.encode())
                elif path == "/rgb.jpg":
                    data = parent._reader.get_rgb_jpeg()
                    if data is None:
                        data = _BLANK_JPEG
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif path == "/depth.jpg":
                    data = parent._reader.get_depth_jpeg()
                    if data is None:
                        data = _BLANK_JPEG
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif path == "/status":
                    snap = parent._reader.get_frames()
                    data = {
                        "connected": snap["connected"],
                        "error": snap["error"],
                        "fps": snap["fps"],
                        "frame_count": snap["frame_count"],
                        "width": parent._reader.width,
                        "height": parent._reader.height,
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
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
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self._server.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Intel RealSense D435 camera viewer")
    parser.add_argument("--serial", default=None, help="Device serial number")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--list-devices", action="store_true",
                        help="List connected RealSense devices and exit")
    parser.add_argument("--demo", action="store_true",
                        help="Run without hardware (synthetic frames)")
    parser.add_argument("--web-port", type=int, default=8093,
                        help="HTTP server port for the GUI")
    args = parser.parse_args()

    if args.list_devices:
        devices = list_realsense_devices()
        if not devices:
            print("No RealSense devices found.")
        else:
            print("Connected RealSense devices:")
            for s in devices:
                print(f"  S/N {s}")
        return

    reader = CameraReader(
        serial=args.serial,
        width=args.width,
        height=args.height,
        fps=args.fps,
        demo=args.demo,
    )
    reader.start()
    server = CameraServer(reader, port=args.web_port)
    print(f"D435 viewer at http://localhost:{args.web_port}")
    if args.demo:
        print("Demo mode — synthetic frames (no hardware)")
    print("Ctrl+C to stop.")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        reader.stop()
        server.stop()
        print("Done.")


if __name__ == "__main__":
    main()
