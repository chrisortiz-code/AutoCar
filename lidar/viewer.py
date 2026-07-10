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
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from .page_html import get_html
from .reader import JETSON_UART, LidarReader, MODEL_BAUD, MODEL_SCAN_TYPE, list_serial_ports

PAGE_HTML = get_html("/scan")


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
                    self.send_header("Access-Control-Allow-Origin", "*")
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
    parser.add_argument("--gpio", action="store_true",
                        help=f"Use Jetson UART GPIO pins ({JETSON_UART}) instead of USB")
    parser.add_argument("--motor-pwm", type=int, default=660,
                        help="Motor PWM (lower = slower spin, default 660)")
    parser.add_argument("--normal", action="store_true",
                        help="Use normal scan mode (fewer points, lower bandwidth)")
    parser.add_argument("--scan-mode", type=int, default=None, choices=[0, 1, 2],
                        help="Express scan mode: 0=Standard, 1=DenseBoost, 2=UltraDense")
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
    if args.gpio:
        serial_port = serial_port or JETSON_UART
        print(f"GPIO mode: using UART {serial_port}")
    elif not args.demo and not serial_port:
        ports = list_serial_ports()
        if len(ports) == 1:
            serial_port = ports[0]
            print(f"Auto-selected port: {serial_port}")
        else:
            parser.error("Specify --port, --gpio, or use --demo. Run with --list-ports to see devices.")

    reader = LidarReader(serial_port or "demo", baudrate=baud, scan_type=scan_type, demo=args.demo, motor_pwm=args.motor_pwm, use_normal_scan=args.normal, express_mode=args.scan_mode)
    reader.start()
    server = ScanServer(reader, port=args.web_port)
    print(f"RPLIDAR viewer at http://localhost:{args.web_port}")
    if args.demo:
        print("Demo mode — synthetic scan (no hardware)")
    else:
        print(f"Serial: {serial_port} @ {baud} baud")
    print("Ctrl+C to stop.")

    # Ensure cleanup runs even on SIGTERM / double Ctrl+C
    import atexit
    import signal

    cleaned = False

    def _cleanup():
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        print("\nCleaning up...")
        reader.stop()
        server.stop()
        print("Done.")

    atexit.register(_cleanup)

    def _sig_handler(signum, frame):
        _cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        _cleanup()


if __name__ == "__main__":
    main()
