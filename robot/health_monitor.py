"""
System health monitor — Jetson thermals, ODrive battery voltage, OOM protection.

Runs as a background thread alongside universal_receiver. Reports status and
can trigger speed limiting or shutdown when thresholds are exceeded.
"""

import os
import platform
import threading
import time

# Jetson thermal thresholds (millidegrees C)
TEMP_WARN_C = 75
TEMP_CRITICAL_C = 85

# ODrive battery voltage thresholds
VBUS_LOW_V = 10.0
VBUS_CRITICAL_V = 9.0

# Check interval
CHECK_INTERVAL = 2.0


def set_oom_priority():
    """Make this process less likely to be killed by Linux OOM killer.
    Lower oom_score_adj = less likely to be killed. -1000 = immune.
    We use -900 for the motor receiver so the kernel kills the RL policy first.
    """
    if platform.system() != "Linux":
        return False
    try:
        with open(f"/proc/{os.getpid()}/oom_score_adj", "w") as f:
            f.write("-900\n")
        print(f"OOM priority set: pid {os.getpid()} oom_score_adj=-900")
        return True
    except PermissionError:
        print("OOM priority: needs root, skipping (run with sudo for protection)")
        return False
    except Exception as e:
        print(f"OOM priority failed: {e}")
        return False


def read_jetson_temps():
    """Read all thermal zones on the Jetson. Returns dict of zone_name -> temp_C."""
    temps = {}
    if platform.system() != "Linux":
        return temps
    base = "/sys/devices/virtual/thermal"
    try:
        zones = [d for d in os.listdir(base) if d.startswith("thermal_zone")]
    except FileNotFoundError:
        return temps
    for zone in sorted(zones):
        try:
            with open(os.path.join(base, zone, "temp")) as f:
                temp_mc = int(f.read().strip())
            with open(os.path.join(base, zone, "type")) as f:
                name = f.read().strip()
            temps[name] = temp_mc / 1000.0
        except Exception:
            pass
    return temps


class HealthMonitor:
    def __init__(self, mc=None):
        self._mc = mc
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.jetson_temps = {}
        self.max_temp_c = 0.0
        self.vbus_min_v = 0.0
        self.vbus_voltages = {}
        self.thermal_state = "ok"  # ok | warn | critical
        self.battery_state = "ok"  # ok | low | critical
        self._speed_scale = 1.0
        self._on_critical = None

    @property
    def speed_scale(self):
        """Multiplier for max speed (1.0 = full, 0.5 = half, 0.0 = stop)."""
        return self._speed_scale

    def on_critical(self, callback):
        """Register callback() for critical events (triggers e-stop)."""
        self._on_critical = callback

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print("HealthMonitor started")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            self._check_thermals()
            self._check_battery()
            self._stop_event.wait(CHECK_INTERVAL)

    def _check_thermals(self):
        temps = read_jetson_temps()
        if not temps:
            return
        with self._lock:
            self.jetson_temps = temps
            self.max_temp_c = max(temps.values()) if temps else 0.0

        max_t = self.max_temp_c
        if max_t >= TEMP_CRITICAL_C:
            if self.thermal_state != "critical":
                print(f"\n!! THERMAL CRITICAL: {max_t:.0f}°C — stopping motors")
                self.thermal_state = "critical"
                self._speed_scale = 0.0
                if self._on_critical:
                    self._on_critical()
        elif max_t >= TEMP_WARN_C:
            if self.thermal_state != "warn":
                print(f"THERMAL WARN: {max_t:.0f}°C — limiting speed to 50%")
            self.thermal_state = "warn"
            self._speed_scale = 0.5
        else:
            if self.thermal_state != "ok":
                print(f"Thermal OK: {max_t:.0f}°C")
            self.thermal_state = "ok"
            self._speed_scale = 1.0

    def _check_battery(self):
        if not self._mc:
            return
        odrives = getattr(self._mc, 'odrives', None)
        if not odrives:
            return
        voltages = {}
        for role, odrv in list(odrives.items()):
            try:
                voltages[role] = round(odrv.vbus_voltage, 2)
            except Exception:
                pass
        if not voltages:
            return

        min_v = min(voltages.values())
        with self._lock:
            self.vbus_voltages = voltages
            self.vbus_min_v = min_v

        if min_v <= VBUS_CRITICAL_V:
            if self.battery_state != "critical":
                print(f"\n!! BATTERY CRITICAL: {min_v:.1f}V — stopping motors")
                self.battery_state = "critical"
                if self._on_critical:
                    self._on_critical()
        elif min_v <= VBUS_LOW_V:
            if self.battery_state != "low":
                print(f"BATTERY LOW: {min_v:.1f}V")
            self.battery_state = "low"
        else:
            self.battery_state = "ok"

    def status_dict(self):
        with self._lock:
            return {
                "jetson_temps": dict(self.jetson_temps),
                "max_temp_c": round(self.max_temp_c, 1),
                "thermal_state": self.thermal_state,
                "vbus_voltages": dict(self.vbus_voltages),
                "vbus_min_v": self.vbus_min_v,
                "battery_state": self.battery_state,
                "speed_scale": self._speed_scale,
            }
