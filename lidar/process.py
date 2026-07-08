"""
LidarProcess — runs LidarReader in a child process to avoid GIL contention
with the camera's JPEG encoding.

Drop-in replacement: exposes the same get_scan() / start() / stop() interface.
"""

import multiprocessing as mp
import time


def _worker(port, baudrate, scan_type, demo, pipe, stop_evt):
    """Child process: run LidarReader and push snapshots over the pipe."""
    from lidar.reader import LidarReader

    reader = LidarReader(port, baudrate=baudrate, scan_type=scan_type, demo=demo)
    reader.start()

    try:
        while not stop_evt.is_set():
            snap = reader.get_scan()
            try:
                pipe.send(snap)
            except (BrokenPipeError, OSError):
                break
            time.sleep(0.05)  # 20 Hz push rate
    finally:
        reader.stop()


class LidarProcess:
    """Runs LidarReader in a separate process, shares scan data via Pipe."""

    def __init__(self, port, *, baudrate=1_000_000, scan_type='express', demo=False):
        self.port = port
        self.baudrate = baudrate
        self.scan_type = scan_type
        self.demo = demo
        self._process = None
        self._parent_pipe = None
        self._stop_evt = None
        self._last_scan = {
            "points": [],
            "count": 0,
            "scan_hz": 0.0,
            "connected": False,
            "port": port,
            "error": None,
        }

    def start(self):
        if self._process and self._process.is_alive():
            return
        self._stop_evt = mp.Event()
        self._parent_pipe, child_pipe = mp.Pipe(duplex=False)
        self._process = mp.Process(
            target=_worker,
            args=(self.port, self.baudrate, self.scan_type, self.demo,
                  child_pipe, self._stop_evt),
            daemon=True,
        )
        self._process.start()
        child_pipe.close()  # parent only reads

    def stop(self):
        if self._stop_evt:
            self._stop_evt.set()
        if self._process:
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.terminate()
            self._process = None
        if self._parent_pipe:
            self._parent_pipe.close()
            self._parent_pipe = None

    def get_scan(self):
        """Return latest scan snapshot. Drains pipe to get freshest data."""
        if self._parent_pipe is None:
            return self._last_scan
        # Drain to latest
        while self._parent_pipe.poll():
            try:
                self._last_scan = self._parent_pipe.recv()
            except (EOFError, OSError):
                break
        return self._last_scan

    @property
    def connected(self):
        return self._last_scan.get("connected", False)

    @property
    def error(self):
        return self._last_scan.get("error")
