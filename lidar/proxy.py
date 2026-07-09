"""
LidarProxy — spawns the standalone lidar viewer as a subprocess and
polls its HTTP /scan endpoint for data.

Drop-in replacement for LidarProcess / LidarReader: exposes the same
get_scan() / start() / stop() interface.  The standalone viewer is the
only code path that produces clean data, so we reuse it exactly as-is.
"""

import json
import subprocess
import sys
import threading
import time
import urllib.request


class LidarProxy:
    """Runs `python -m lidar.viewer` as a subprocess on its own port,
    polls /scan for fresh data."""

    def __init__(self, port, *, demo=False, viewer_port=8092):
        self.port = port          # serial port (or "demo")
        self.demo = demo
        self.viewer_port = viewer_port
        self._process = None
        self._poll_thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_scan = {
            "points": [],
            "count": 0,
            "scan_hz": 0.0,
            "connected": False,
            "port": port,
            "error": None,
        }

    def start(self):
        if self._process and self._process.poll() is None:
            return

        # Build command to launch the standalone viewer
        cmd = [
            sys.executable, "-m", "lidar.viewer",
            "--web-port", str(self.viewer_port),
        ]
        if self.demo:
            cmd.append("--demo")
        else:
            cmd.extend(["--port", self.port])

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Start polling thread
        self._stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop(self):
        self._stop.set()
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def _poll_loop(self):
        """Poll the viewer's /scan endpoint and cache the result."""
        url = f"http://127.0.0.1:{self.viewer_port}/scan"
        # Wait for viewer to start up
        time.sleep(1.0)
        while not self._stop.is_set():
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    data = json.loads(resp.read())
                with self._lock:
                    self._last_scan = data
            except Exception:
                pass
            time.sleep(0.05)  # 20 Hz poll

    def get_scan(self):
        with self._lock:
            return dict(self._last_scan)

    @property
    def connected(self):
        with self._lock:
            return self._last_scan.get("connected", False)

    @property
    def error(self):
        with self._lock:
            return self._last_scan.get("error")
