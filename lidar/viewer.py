"""
RPLIDAR S2 live scan viewer — polar plot web UI.

Shows a real-time 360° scan from an S2 (or other RPLIDAR) over USB serial.

Usage:
    python -m lidar.viewer --list-ports
    python -m lidar.viewer --port COM3
    python -m lidar.viewer --demo
    python -m lidar.viewer --port /dev/ttyUSB0 --web-port 8092
"""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from .reader import LidarReader, MODEL_BAUD, MODEL_SCAN_TYPE, list_serial_ports

PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>RPLIDAR Viewer</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #1a1a1a; color: #d4d4d4;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         display: flex; flex-direction: column; align-items: center;
         min-height: 100vh; padding: 16px; }
  header { display: flex; align-items: baseline; gap: 12px; width: min(640px, 96vw);
           margin-bottom: 8px; }
  header h1 { font-size: 14px; font-weight: 600; color: #e0e0e0; }
  #badge { font-size: 11px; margin-left: auto; }
  #badge.ok { color: #5a9a5a; }
  #badge.err { color: #c87830; }
  #plot-wrap { position: relative; border: 1px solid #333; background: #111; }
  canvas { display: block; }
  #stats { margin-top: 8px; width: min(640px, 96vw); font-size: 12px; color: #888;
           display: flex; flex-wrap: wrap; gap: 14px 18px; padding: 6px 0;
           border-bottom: 1px solid #2a2a2a; }
  #stats .v { color: #b0b0b0; }
  #error { margin-top: 8px; width: min(640px, 96vw); font-size: 12px; color: #c87830;
           min-height: 18px; }
  #legend { margin-top: 8px; width: min(640px, 96vw); font-size: 11px; color: #666; }
  .bar { height: 8px; width: 160px; display: inline-block; vertical-align: middle;
         margin-left: 8px; border-radius: 2px;
         background: linear-gradient(90deg, #e6194b, #ffe119, #3cb44b); }
</style>
</head>
<body>
  <header>
    <h1>RPLIDAR Scan</h1>
    <span id="badge" class="err">connecting</span>
  </header>
  <div id="plot-wrap">
    <canvas id="plot" width="640" height="640"></canvas>
  </div>
  <div id="stats">
    <span>points <span class="v" id="s-cnt">0</span></span>
    <span>scan <span class="v" id="s-hz">--</span> Hz</span>
    <span>range <span class="v" id="s-range">--</span> m</span>
    <span>port <span class="v" id="s-port">--</span></span>
  </div>
  <div id="error"></div>
  <div id="legend">near <span class="bar"></span> far</div>
<script>
const canvas = document.getElementById('plot');
const ctx = canvas.getContext('2d');
const badge = document.getElementById('badge');
const sCnt = document.getElementById('s-cnt');
const sHz = document.getElementById('s-hz');
const sRange = document.getElementById('s-range');
const sPort = document.getElementById('s-port');
const errEl = document.getElementById('error');

const W = canvas.width;
const H = canvas.height;
const CX = W / 2;
const CY = H / 2;
const R_MAX = Math.min(W, H) * 0.46;
let rangeM = 1;  // auto-scaled each frame

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
  const targetM = dataMaxM * 1.1;  // 10% margin
  // Smooth transitions: ease toward target
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

async function poll() {
  try {
    const r = await fetch('/scan');
    const s = await r.json();
    sCnt.textContent = s.count;
    sHz.textContent = s.scan_hz ? s.scan_hz.toFixed(1) : '--';
    sPort.textContent = s.port || '--';
    if (s.min_mm != null && s.max_mm != null) {
      sRange.textContent = (s.min_mm / 1000).toFixed(2) + ' – ' + (s.max_mm / 1000).toFixed(2);
    } else {
      sRange.textContent = '--';
    }
    if (s.connected) {
      badge.textContent = 'live';
      badge.className = 'ok';
      errEl.textContent = '';
    } else {
      badge.textContent = 'disconnected';
      badge.className = 'err';
      errEl.textContent = s.error || 'waiting for lidar...';
    }
    drawScan(s.points || []);
  } catch (e) {}
}

setInterval(poll, 100);
poll();
</script>
</body>
</html>"""


class ScanServer:
    def __init__(self, reader, port=8092):
        self._reader = reader
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
                elif path == "/scan":
                    snap = parent._reader.get_scan()
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
    parser = argparse.ArgumentParser(description="RPLIDAR S2 polar scan viewer")
    parser.add_argument("--port", dest="serial_port", default=None,
                        help="Serial port (e.g. COM3, /dev/ttyUSB0)")
    parser.add_argument("--model", default="s2", choices=sorted(MODEL_BAUD),
                        help="Lidar model for default baud rate")
    parser.add_argument("--baudrate", type=int, default=None,
                        help="Override serial baud rate")
    parser.add_argument("--list-ports", action="store_true",
                        help="List available serial ports and exit")
    parser.add_argument("--demo", action="store_true",
                        help="Run without hardware (synthetic scan)")
    parser.add_argument("--web-port", type=int, default=8092,
                        help="HTTP server port for the GUI")
    args = parser.parse_args()

    if args.list_ports:
        ports = list_serial_ports()
        if not ports:
            print("No serial ports found.")
        else:
            print("Available serial ports:")
            for p in ports:
                print(f"  {p}")
        return

    baud = args.baudrate or MODEL_BAUD[args.model]
    scan_type = MODEL_SCAN_TYPE[args.model]
    serial_port = args.serial_port
    if not args.demo and not serial_port:
        ports = list_serial_ports()
        if len(ports) == 1:
            serial_port = ports[0]
            print(f"Auto-selected port: {serial_port}")
        else:
            parser.error("Specify --port or use --demo. Run with --list-ports to see devices.")

    reader = LidarReader(serial_port or "demo", baudrate=baud, scan_type=scan_type, demo=args.demo)
    reader.start()
    server = ScanServer(reader, port=args.web_port)
    print(f"RPLIDAR viewer at http://localhost:{args.web_port}")
    if args.demo:
        print("Demo mode — synthetic scan (no hardware)")
    else:
        print(f"Serial: {serial_port} @ {baud} baud")
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
