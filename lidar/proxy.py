"""
LidarProxy — spawns the standalone lidar viewer as a subprocess and
polls its HTTP /scan endpoint for data.

Drop-in replacement for LidarProcess / LidarReader: exposes the same
get_scan() / start() / stop() interface.
"""

import json
import os
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

        # Use project root as cwd so `lidar.viewer` module resolves
        project_root = os.path.join(os.path.dirname(__file__), "..")

        print(f"[LidarProxy] Starting: {' '.join(cmd)}")
        self._process = subprocess.Popen(
            cmd,
            cwd=project_root,
            stdout=sys.stdout,   # show viewer output
            stderr=sys.stderr,   # show viewer errors
        )
        print(f"[LidarProxy] PID {self._process.pid}")

        # Start polling thread
        self._stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop(self):
        self._stop.set()
        if self._process:
            print(f"[LidarProxy] Stopping PID {self._process.pid}")
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def _poll_loop(self):
        """Poll the viewer's /scan endpoint and cache the result."""
        url = f"http://127.0.0.1:{self.viewer_port}/scan"

        # Wait for viewer HTTP server to come up
        for attempt in range(30):
            if self._stop.is_set():
                return
            # Check if process died
            if self._process and self._process.poll() is not None:
                rc = self._process.returncode
                print(f"[LidarProxy] Viewer process died with code {rc}")
                with self._lock:
                    self._last_scan["error"] = f"viewer exited ({rc})"
                return
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    data = json.loads(resp.read())
                with self._lock:
                    self._last_scan = data
                print(f"[LidarProxy] Connected to viewer on :{self.viewer_port}")
                break
            except Exception:
                time.sleep(0.5)
        else:
            print(f"[LidarProxy] Failed to connect to viewer after 15s")
            return

        # Main poll loop
        while not self._stop.is_set():
            # Check if process is still alive
            if self._process and self._process.poll() is not None:
                rc = self._process.returncode
                print(f"[LidarProxy] Viewer process died with code {rc}, restarting...")
                with self._lock:
                    self._last_scan["connected"] = False
                    self._last_scan["error"] = f"viewer crashed ({rc})"
                # Restart the viewer
                self._restart()
                # Wait for it to come back
                time.sleep(3)
                continue

            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    data = json.loads(resp.read())
                with self._lock:
                    self._last_scan = data
            except Exception as e:
                pass  # transient HTTP errors are fine
            time.sleep(0.05)  # 20 Hz poll

    def _restart(self):
        """Restart the viewer subprocess."""
        if self._process:
            try:
                self._process.kill()
            except Exception:
                pass
        cmd = [
            sys.executable, "-m", "lidar.viewer",
            "--web-port", str(self.viewer_port),
        ]
        if self.demo:
            cmd.append("--demo")
        else:
            cmd.extend(["--port", self.port])

        project_root = os.path.join(os.path.dirname(__file__), "..")
        print(f"[LidarProxy] Restarting viewer...")
        self._process = subprocess.Popen(
            cmd,
            cwd=project_root,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

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
