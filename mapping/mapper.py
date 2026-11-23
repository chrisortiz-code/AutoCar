"""
Mapper daemon — fuses lidar scans + wheel odometry into an occupancy grid.

Runs on the Jetson alongside universal_receiver.py. Connects to:
  - LidarReader for scan data
  - MecanumUSB (or CAN) motor controller for encoder positions

Usage:
    python -m mapping.mapper --demo
    python -m mapping.mapper --lidar-port /dev/ttyUSB0 --transport can
"""

import argparse
import json
import math
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from mapping.grid import OccupancyGrid
from mapping.odometry import WheelOdometry

VIEWER_HTML = None


def _get_viewer_html(map_endpoint="/map", pose_endpoint="/pose"):
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AutoCar Map</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#111; color:#eee; font-family:monospace; display:flex;
         flex-direction:column; align-items:center; height:100vh; }}
  h1 {{ font-size:1.2em; padding:8px; color:#0f0; }}
  #info {{ font-size:0.85em; color:#888; padding:4px; }}
  canvas {{ border:1px solid #333; margin:8px; image-rendering:pixelated; }}
</style>
</head><body>
<h1>AutoCar Occupancy Map</h1>
<div id="info">Waiting for data...</div>
<canvas id="map" width="600" height="600"></canvas>
<script>
const canvas = document.getElementById('map');
const ctx = canvas.getContext('2d');
const info = document.getElementById('info');

let mapImg = new Image();
let pose = null;

async function update() {{
  try {{
    const ts = Date.now();
    const resp = await fetch('{map_endpoint}?t=' + ts);
    if (resp.ok) {{
      const blob = await resp.blob();
      mapImg.src = URL.createObjectURL(blob);
    }}
    const presp = await fetch('{pose_endpoint}?t=' + ts);
    if (presp.ok) pose = await presp.json();
  }} catch(e) {{}}
}}

mapImg.onload = function() {{
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(mapImg, 0, 0, canvas.width, canvas.height);

  if (pose) {{
    info.textContent = `x=${{pose.x_cm.toFixed(1)}}cm  y=${{pose.y_cm.toFixed(1)}}cm  θ=${{pose.theta_deg.toFixed(1)}}°  scans=${{pose.scans}}`;

    // Draw robot heading arrow
    const scale = canvas.width / pose.grid_size;
    const cx = pose.cell_col * scale;
    const cy = pose.cell_row * scale;
    const len = 12;
    const angle = -pose.theta_rad;  // canvas Y is flipped

    ctx.beginPath();
    ctx.arc(cx, cy, 5, 0, 2*Math.PI);
    ctx.fillStyle = '#0f0';
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + len*Math.cos(angle), cy + len*Math.sin(angle));
    ctx.strokeStyle = '#0f0';
    ctx.lineWidth = 2;
    ctx.stroke();
  }}
}};

setInterval(update, 500);
update();
</script>
</body></html>"""


class Mapper:
    def __init__(self, grid=None, odometry=None, lidar_reader=None, motor_ctrl=None):
        self.grid = grid or OccupancyGrid()
        self.odom = odometry
        self.lidar = lidar_reader
        self.motors = motor_ctrl
        self._stop = threading.Event()
        self._thread = None
        self._scan_count = 0

    @property
    def scan_count(self):
        return self._scan_count

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        last_update = 0
        while not self._stop.is_set():
            if self.motors and self.odom:
                self.odom.update(self.motors.positions)

            if self.lidar:
                snap = self.lidar.get_scan()
                if snap["points"] and snap["connected"]:
                    x, y, theta = self.odom.pose if self.odom else (0.0, 0.0, 0.0)
                    self.grid.insert_scan(x, y, theta, snap["points"])
                    self._scan_count += 1

            self._stop.wait(0.15)


class MapServer:
    def __init__(self, mapper, port=8093):
        self._mapper = mapper
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
                    html = _get_viewer_html()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(html.encode())
                elif path == "/map":
                    m = parent._mapper
                    x, y, _ = m.odom.pose if m.odom else (0, 0, 0)
                    png = m.grid.to_png_bytes(robot_x=x, robot_y=y)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(png)
                elif path == "/pose":
                    m = parent._mapper
                    x, y, theta = m.odom.pose if m.odom else (0, 0, 0)
                    r, c = m.grid.world_to_cell(x, y)
                    data = {
                        "x_cm": round(x, 1),
                        "y_cm": round(y, 1),
                        "theta_rad": round(theta, 3),
                        "theta_deg": round(math.degrees(theta), 1),
                        "cell_row": r,
                        "cell_col": c,
                        "grid_size": m.grid.size,
                        "scans": m.scan_count,
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(json.dumps(data).encode())
                elif path == "/grid":
                    prob = self._mapper.grid.probability_map()
                    # Return raw probability grid as JSON (for external consumers)
                    data = {
                        "resolution_cm": self._mapper.grid.resolution,
                        "size": self._mapper.grid.size,
                        "origin_cell": self._mapper.grid.origin_cell,
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


class DemoMapper(Mapper):
    """Simulates a robot driving in a square room with lidar."""

    def __init__(self):
        super().__init__(grid=OccupancyGrid())
        self._sim_x = 0.0
        self._sim_y = 0.0
        self._sim_theta = 0.0
        self.odom = _DummyOdom(self)

    def _run(self):
        walls = self._make_room(400, 300)  # 4m x 3m room
        t = 0
        while not self._stop.is_set():
            # Robot drives in a slow circle
            self._sim_x = 50 * math.cos(t * 0.3)
            self._sim_y = 50 * math.sin(t * 0.3)
            self._sim_theta = t * 0.3 + math.pi / 2

            scan = self._simulate_scan(walls)
            self.grid.insert_scan(self._sim_x, self._sim_y, self._sim_theta, scan)
            self._scan_count += 1
            t += 0.2
            self._stop.wait(0.15)

    def _make_room(self, w, h):
        """Generate wall segments as list of (x1,y1,x2,y2) in cm."""
        hw, hh = w / 2, h / 2
        return [
            (-hw, -hh, hw, -hh),   # bottom
            (hw, -hh, hw, hh),     # right
            (hw, hh, -hw, hh),     # top
            (-hw, hh, -hw, -hh),   # left
            # Add an obstacle: a box in the room
            (60, 40, 100, 40),
            (100, 40, 100, 80),
            (100, 80, 60, 80),
            (60, 80, 60, 40),
        ]

    def _simulate_scan(self, walls, num_rays=360):
        """Cast rays and return synthetic scan points."""
        points = []
        for i in range(num_rays):
            angle_deg = i * 360.0 / num_rays
            angle_rad = self._sim_theta + math.radians(angle_deg)
            dx = math.cos(angle_rad)
            dy = math.sin(angle_rad)

            best_dist = 12000.0  # max range mm
            for x1, y1, x2, y2 in walls:
                d = self._ray_segment_intersect(
                    self._sim_x, self._sim_y, dx, dy, x1, y1, x2, y2
                )
                if d is not None and d < best_dist:
                    best_dist = d

            if best_dist < 12000.0:
                points.append({"angle": angle_deg, "dist_mm": int(best_dist)})
        return points

    @staticmethod
    def _ray_segment_intersect(ox, oy, dx, dy, x1, y1, x2, y2):
        """Ray-segment intersection, returns distance in mm or None."""
        sx, sy = x2 - x1, y2 - y1
        denom = dx * sy - dy * sx
        if abs(denom) < 1e-10:
            return None
        t = ((x1 - ox) * sy - (y1 - oy) * sx) / denom
        u = ((x1 - ox) * dy - (y1 - oy) * dx) / denom
        if t > 0 and 0 <= u <= 1:
            return t * 10.0  # cm to mm
        return None


class _DummyOdom:
    def __init__(self, demo):
        self._demo = demo

    @property
    def pose(self):
        return self._demo._sim_x, self._demo._sim_y, self._demo._sim_theta


def main():
    parser = argparse.ArgumentParser(description="AutoCar occupancy grid mapper")
    parser.add_argument("--demo", action="store_true",
                        help="Run with simulated robot and room")
    parser.add_argument("--lidar-port", default=None,
                        help="Serial port for RPLIDAR")
    parser.add_argument("--lidar-model", default="s2",
                        help="Lidar model (s2, a1, etc.)")
    parser.add_argument("--transport", default="auto", choices=["can", "usb", "auto"],
                        help="ODrive transport")
    parser.add_argument("--web-port", type=int, default=8093,
                        help="HTTP server port for map viewer")
    parser.add_argument("--resolution", type=float, default=5.0,
                        help="Grid resolution in cm/cell (default 5)")
    parser.add_argument("--grid-size", type=int, default=800,
                        help="Grid side length in cells (default 800)")
    parser.add_argument("--diff-drive", action="store_true",
                        help="2-wheel differential drive mode")
    args = parser.parse_args()

    if args.demo:
        print("Demo mode — simulated robot in a room")
        mapper = DemoMapper()
        mapper.grid = OccupancyGrid(resolution=args.resolution, size=args.grid_size)
        mapper.start()
        server = MapServer(mapper, port=args.web_port)
        print(f"Map viewer at http://localhost:{args.web_port}")
    else:
        from lidar.reader import LidarReader, MODEL_BAUD, MODEL_SCAN_TYPE, list_serial_ports

        lidar_port = args.lidar_port
        if not lidar_port:
            ports = list_serial_ports()
            if len(ports) == 1:
                lidar_port = ports[0]
                print(f"Auto-selected lidar port: {lidar_port}")
            else:
                parser.error("Specify --lidar-port or connect exactly one serial device")

        baud = MODEL_BAUD[args.lidar_model]
        scan_type = MODEL_SCAN_TYPE[args.lidar_model]
        lidar = LidarReader(lidar_port, baudrate=baud, scan_type=scan_type)
        lidar.start()

        from robot.motor_control import MecanumUSB, ROLE_DIR
        from robot.constants import CM_PER_MOTOR_REV, TURNS_PER_DEG

        can_iface = None
        if args.transport == "can" or (args.transport == "auto"):
            try:
                from robot.universal_receiver import _socketcan_interface
                can_iface = _socketcan_interface()
            except Exception:
                pass

        motors = MecanumUSB(diff_drive=args.diff_drive, can_interface=can_iface)
        motors.connect()
        odom = WheelOdometry(CM_PER_MOTOR_REV, TURNS_PER_DEG, ROLE_DIR)
        grid = OccupancyGrid(resolution=args.resolution, size=args.grid_size)

        mapper = Mapper(grid=grid, odometry=odom, lidar_reader=lidar, motor_ctrl=motors)
        mapper.start()
        server = MapServer(mapper, port=args.web_port)
        print(f"Map viewer at http://localhost:{args.web_port}")

    import atexit
    import signal

    cleaned = False

    def _cleanup():
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        print("\nStopping mapper...")
        mapper.stop()
        server.stop()
        if not args.demo:
            lidar.stop()
        print("Done.")

    atexit.register(_cleanup)
    signal.signal(signal.SIGTERM, lambda *a: (_cleanup(), sys.exit(0)))
    signal.signal(signal.SIGINT, lambda *a: (_cleanup(), sys.exit(0)))

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _cleanup()


if __name__ == "__main__":
    main()
