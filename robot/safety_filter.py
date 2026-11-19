"""
Lidar-based safety override — reactive obstacle avoidance using repulsive
vector fields.  Polls the lidar viewer's /scan endpoint in a background thread
and filters drive commands before they reach the motors.

Usage:
    safety = SafetyFilter(enabled=True)
    safety.start()
    vx, vy, spd, rot, action = safety.filter_drive(vx, vy, spd, rot)
    safety.stop()
"""

import json
import math
import threading
import time
import urllib.request

LIDAR_URL = "http://127.0.0.1:8092/scan"
POLL_HZ = 10  # lidar scans at ~10 Hz, no point polling faster
SCAN_TIMEOUT = 0.5  # seconds before scan is considered stale


def _angle_diff(a, b):
    """Signed shortest angular difference (a - b), result in -180..180."""
    d = (a - b) % 360
    if d > 180:
        d -= 360
    return d


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class SafetyFilter:
    def __init__(self, enabled=True, min_dist_mm=250, slow_dist_mm=800,
                 fov_half_deg=60, block_threshold=0.8, deflect_gain=1.5,
                 min_quality=10):
        self.enabled = enabled
        self.min_dist_mm = min_dist_mm
        self.slow_dist_mm = slow_dist_mm
        self.fov_half_deg = fov_half_deg
        self.block_threshold = block_threshold
        self.deflect_gain = deflect_gain
        self.min_quality = min_quality

        self.force_on_sources = {"rl"}

        self._scan = None
        self._scan_time = 0.0
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()

        self._last_action = "idle"
        self._nearest_mm = -1
        self._connected = False

    # ── Properties ───────────────────────────────────────────────────

    @property
    def lidar_connected(self):
        return self._connected

    @property
    def scan_hz(self):
        return POLL_HZ

    @property
    def nearest_obstacle_mm(self):
        return self._nearest_mm

    @property
    def last_action(self):
        return self._last_action

    # ── Background poll thread ───────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        print(f"SafetyFilter started (enabled={self.enabled})")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        print("SafetyFilter stopped")

    def _poll_loop(self):
        interval = 1.0 / POLL_HZ
        while not self._stop_event.is_set():
            try:
                req = urllib.request.Request(LIDAR_URL, method="GET")
                with urllib.request.urlopen(req, timeout=0.3) as resp:
                    data = json.loads(resp.read())
                with self._lock:
                    self._scan = data.get("points", [])
                    self._scan_time = time.time()
                    self._connected = True
            except Exception:
                self._connected = False
            self._stop_event.wait(interval)

    def set_scan(self, points):
        """Push scan data directly, bypassing HTTP polling."""
        with self._lock:
            self._scan = points
            self._scan_time = time.time()
            self._connected = True

    # ── Core filter ──────────────────────────────────────────────────

    def filter_drive(self, vx, vy, trans_speed, rot_speed, source="manual"):
        try:
            return self._filter_impl(vx, vy, trans_speed, rot_speed, source)
        except Exception as e:
            print(f"SafetyFilter error: {e}")
            self._last_action = "error"
            return vx, vy, trans_speed, rot_speed, "error"

    def _filter_impl(self, vx, vy, trans_speed, rot_speed, source):
        # Check if filtering applies
        if not self.enabled and source not in self.force_on_sources:
            self._last_action = "disabled"
            return vx, vy, trans_speed, rot_speed, "disabled"

        # Get current scan
        with self._lock:
            scan = self._scan
            scan_age = time.time() - self._scan_time

        if scan is None or scan_age > SCAN_TIMEOUT:
            self._last_action = "no_scan"
            return vx, vy, trans_speed, rot_speed, "no_scan"

        # Zero movement -> nothing to filter
        if abs(vx) < 1e-6 and abs(vy) < 1e-6:
            self._last_action = "idle"
            return vx, vy, trans_speed, rot_speed, "idle"

        # Compute movement heading (degrees, 0=right, CCW)
        heading = math.degrees(math.atan2(-vy, vx)) % 360

        # Gather repulsive vectors
        repulse_x = 0.0
        repulse_y = 0.0
        nearest = 999999

        for pt in scan:
            angle = pt.get("angle", 0.0)
            dist = pt.get("dist_mm", 0)
            quality = pt.get("quality", 0)

            if quality < self.min_quality or dist <= 0:
                continue

            if dist < nearest:
                nearest = dist

            offset = _angle_diff(angle, heading)
            if abs(offset) > self.fov_half_deg:
                continue

            # Within FOV
            if dist >= self.slow_dist_mm:
                continue

            cos_weight = math.cos(math.radians(offset))
            proximity = _clamp(
                (self.slow_dist_mm - dist) / (self.slow_dist_mm - self.min_dist_mm),
                0.0, 1.0
            )
            magnitude = proximity * cos_weight

            # Repulsive direction: away from obstacle
            away_rad = math.radians(angle + 180)
            repulse_x += math.cos(away_rad) * magnitude
            repulse_y += math.sin(away_rad) * magnitude

        self._nearest_mm = nearest if nearest < 999999 else -1

        net_mag = math.hypot(repulse_x, repulse_y)

        if net_mag >= self.block_threshold:
            # Full block
            self._last_action = "block"
            return vx, vy, 0.0, rot_speed, "block"

        if net_mag > 0:
            # Deflect and slow
            dvx = vx + repulse_x * self.deflect_gain
            dvy = vy + repulse_y * self.deflect_gain
            norm = math.hypot(dvx, dvy)
            if norm > 1e-6:
                dvx /= norm
                dvy /= norm
            scale = max(0.0, 1.0 - net_mag)
            self._last_action = f"deflect:{scale:.2f}"
            return dvx, dvy, trans_speed * scale, rot_speed, f"deflect:{scale:.2f}"

        self._last_action = "clear"
        return vx, vy, trans_speed, rot_speed, "clear"
