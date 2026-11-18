"""
Shared USB / ODrive / mecanum motor interface.
Drop-in replacement for can_bus.py — same public API, USB transport.
Each ODrive is a separate USB device with one motor on axis0.

First run:  python robot/identify_motors.py
  -> discovers all ODrives, lets you map serial numbers to wheel roles,
     saves robot/motor_config.json

After that, connect() loads the config and connects by serial number.
"""

import json
import math
import os
import threading
import time

import odrive
from odrive.enums import (
    AXIS_STATE_IDLE,
    AXIS_STATE_CLOSED_LOOP_CONTROL,
    CONTROL_MODE_VELOCITY_CONTROL,
    INPUT_MODE_PASSTHROUGH,
    INPUT_MODE_VEL_RAMP,
)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "motor_config.json")

# ── Motor layout ────────────────────────────────────────────────────
ROLES = ["FL", "FR", "BL", "BR"]
ROLE_DIR = {
    "FL": -1,
    "FR":  1,
    "BL": -1,
    "BR":  1,
}
ALL_ROLES = list(ROLES)

# ── Defaults ────────────────────────────────────────────────────────
MAX_VEL       = 10.0   # turns/s velocity limit
CURRENT_LIMIT = 30.0   # amps
VEL_RAMP_RATE = 10.0   # turns/s^2 — how fast velocity changes (higher = snappier)
SCAN_TIMEOUT  = 10     # seconds per ODrive discovery

# ── Drive constants ─────────────────────────────────────────────────
WHEEL_DIAMETER     = 11.75      # cm
GEAR_RATIO         = 16.0 / 90.0
CM_PER_MOTOR_REV   = GEAR_RATIO * math.pi * WHEEL_DIAMETER
MOTOR_REVS_PER_CM  = 1.0 / CM_PER_MOTOR_REV
TURNS_PER_DEG      = 0.067
POS_TOLERANCE      = 0.1
MOVE_TIMEOUT       = 30
RAMP_PCT           = 0.15

DIFF_DRIVE_ROLES = ["FL", "FR"]

CURRENT_SPIKE_THRESHOLD = 8.0


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(mapping):
    with open(CONFIG_PATH, "w") as f:
        json.dump(mapping, f, indent=2)
    print(f"Config saved to {CONFIG_PATH}")


class MecanumUSB:
    def __init__(self, max_vel=MAX_VEL, current_limit=CURRENT_LIMIT,
                 diff_drive=False):
        self.odrives = {}        # role -> odrive object
        self.serials = {}        # role -> serial number string
        self.connected = set()   # set of roles
        self.armed = set()       # set of roles
        self.diff_drive = diff_drive
        self.active_roles = DIFF_DRIVE_ROLES if diff_drive else ALL_ROLES
        self.positions = {r: 0.0 for r in ALL_ROLES}
        self.currents = {r: 0.0 for r in ALL_ROLES}
        self.errors = {r: 0 for r in ALL_ROLES}
        self.axis_states = {r: 0 for r in ALL_ROLES}
        self.max_vel = max_vel
        self.current_limit = current_limit
        self._closed = True
        self._poll_thread = None
        self._fault_callback = None
        self._last_fault_time = 0.0

    # ── Discovery ───────────────────────────────────────────────────
    def discover_all(self, count=4, timeout=SCAN_TIMEOUT):
        """Find all connected ODrives. Returns list of (serial, odrv) tuples."""
        print(f"Searching for {count} ODrives over USB...")
        try:
            devices = odrive.find_sync(count=count, timeout=timeout)
        except TimeoutError:
            print("  Timed out before finding all ODrives.")
            devices = []

        if not isinstance(devices, (list, tuple)):
            devices = [devices]

        found = []
        for odrv in devices:
            sn = str(odrv.serial_number)
            found.append((sn, odrv))
            print(f"  Found ODrive serial={sn}")

        return found

    # ── Polling thread ──────────────────────────────────────────────
    def _poller(self):
        """Background thread: poll positions, currents, errors from each ODrive."""
        while not self._closed:
            for role, odrv in list(self.odrives.items()):
                try:
                    ax = odrv.axis0
                    self.positions[role] = round(ax.pos_estimate, 3)
                    self.currents[role] = round(
                        ax.motor.foc.Iq_measured, 3
                    )
                    err = ax.active_errors
                    prev_error = self.errors.get(role, 0)
                    self.errors[role] = err
                    self.axis_states[role] = ax.current_state

                    if err != 0 and prev_error == 0 and self._fault_callback:
                        now = time.time()
                        if now - self._last_fault_time > 3.0:
                            self._last_fault_time = now
                            self._fault_callback(role, err)
                except Exception:
                    pass
            time.sleep(0.05)

    # ── Connection ──────────────────────────────────────────────────
    def on_fault(self, callback):
        """Register callback(role, error_code) called on fault."""
        self._fault_callback = callback

    def connect(self):
        config = load_config()
        if config is None:
            print(f"No motor config found at {CONFIG_PATH}")
            print("Run  python robot/identify_motors.py  first to map ODrives to wheels.")
            return

        self._closed = False
        self.connected.clear()
        self.armed.clear()
        self.serials = dict(config)

        needed = {role: sn for role, sn in self.serials.items()
                  if role in self.active_roles}
        count = len(needed)
        sn_to_role = {sn: role for role, sn in needed.items()}

        print(f"Discovering {count} ODrives over USB...")
        try:
            devices = odrive.find_sync(count=count, timeout=SCAN_TIMEOUT)
        except TimeoutError:
            print("  Timed out before finding all ODrives.")
            devices = []

        if not isinstance(devices, (list, tuple)):
            devices = [devices]

        for odrv in devices:
            sn = str(odrv.serial_number)
            role = sn_to_role.get(sn)
            if role:
                self.odrives[role] = odrv
                self.connected.add(role)
                print(f"  {role} connected (serial={sn})")
            else:
                print(f"  Unknown ODrive serial={sn}, not in config")

        for role in needed:
            if role not in self.connected:
                print(f"  WARNING: {role} (serial={needed[role]}) not found!")

        print(f"Connected: {sorted(self.connected)}")
        if not self.connected:
            print("WARNING: No ODrives found.")
            return

        print("Clearing any pre-existing ODrive errors...")
        self.clear_all_errors()

        self._poll_thread = threading.Thread(target=self._poller, daemon=True)
        self._poll_thread.start()

    def shutdown(self):
        if self._closed and not self.odrives:
            return

        print("Shutting down...")
        self._closed = True

        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=2.0)
        self._poll_thread = None

        for role in list(self.armed):
            try:
                self.odrives[role].axis0.controller.input_vel = 0.0
            except Exception:
                pass
        time.sleep(0.05)
        for role in list(self.armed):
            try:
                self.odrives[role].axis0.requested_state = AXIS_STATE_IDLE
            except Exception:
                pass

        self.odrives.clear()
        self.armed.clear()
        self.connected.clear()
        print("USB shutdown complete.")

    # ── Motor control ───────────────────────────────────────────────
    def clear_errors(self, role):
        if role not in self.odrives:
            return
        try:
            self.odrives[role].clear_errors()
            self.errors[role] = 0
            time.sleep(0.1)
        except Exception as e:
            print(f"Clear errors failed for {role}: {e}")

    def clear_all_errors(self):
        for role in self.connected:
            self.clear_errors(role)

    def estop_and_recover(self):
        if not self.odrives:
            print("[recovery] No ODrives connected, cannot recover")
            return False
        for role in list(self.armed):
            try:
                self.odrives[role].axis0.controller.input_vel = 0.0
            except Exception:
                print(f"[recovery] Failed to zero {role}")
                return False
        time.sleep(0.1)
        for role in list(self.armed):
            try:
                self.odrives[role].axis0.requested_state = AXIS_STATE_IDLE
            except Exception:
                pass
        self.armed.clear()
        time.sleep(0.3)
        self.clear_all_errors()
        time.sleep(0.2)
        self.arm_all()
        return True

    def arm(self, role):
        if role in self.armed or role not in self.odrives:
            return
        odrv = self.odrives[role]
        ax = odrv.axis0

        errors = ax.active_errors
        if errors:
            print(f"  {role}: clearing errors (0x{errors:X}) before arming...")
            odrv.clear_errors()
            time.sleep(0.2)

        ax.controller.config.control_mode = CONTROL_MODE_VELOCITY_CONTROL
        ax.controller.config.input_mode = INPUT_MODE_VEL_RAMP
        ax.controller.config.vel_ramp_rate = VEL_RAMP_RATE
        ax.config.motor.current_soft_max = self.current_limit
        ax.controller.config.vel_limit = self.max_vel
        time.sleep(0.05)
        ax.requested_state = AXIS_STATE_CLOSED_LOOP_CONTROL
        time.sleep(0.3)

        state = ax.current_state
        errors = ax.active_errors
        if state != 8:  # CLOSED_LOOP_CONTROL = 8
            print(f"  WARNING: {role} failed to arm! state={state}, errors=0x{errors:X}")
        else:
            self.armed.add(role)
            print(f"  {role} armed.")

    def arm_all(self):
        for role in self.connected:
            if role in self.active_roles:
                self.arm(role)

    def set_vel(self, role, velocity):
        if role not in self.connected:
            return
        actual = velocity * ROLE_DIR[role]
        try:
            self.odrives[role].axis0.controller.input_vel = actual
        except Exception:
            pass

    def stop_motor(self, role):
        if role not in self.odrives:
            return
        try:
            self.odrives[role].axis0.controller.input_vel = 0.0
            time.sleep(0.05)
            self.odrives[role].axis0.requested_state = AXIS_STATE_IDLE
        except Exception:
            pass
        self.armed.discard(role)

    def stop_all(self):
        for role in ALL_ROLES:
            if role in self.connected:
                self.stop_motor(role)

    def zero_vel(self):
        for role in self.active_roles:
            if role in self.connected:
                self.set_vel(role, 0.0)

    def request_iq(self, role):
        pass

    def request_all_iq(self):
        pass

    # ── Mecanum kinematics ──────────────────────────────────────────
    @staticmethod
    def mecanum_speeds(vx, vy, omega):
        return {
            "FL": vx - vy - omega,
            "FR": vx + vy + omega,
            "BL": vx + vy - omega,
            "BR": vx - vy + omega,
        }

    @staticmethod
    def differential_speeds(vx, omega):
        return {"FL": vx + omega, "FR": vx - omega}

    def drive(self, vx, vy, trans_speed, rot_speed):
        if self.diff_drive:
            self._drive_diff(vx, trans_speed, rot_speed)
        else:
            self._drive_mecanum(vx, vy, trans_speed, rot_speed)

    def _drive_diff(self, vx, trans_speed, rot_speed):
        trans = {r: 0.0 for r in DIFF_DRIVE_ROLES}
        if trans_speed > 0.01:
            raw = self.differential_speeds(vx, 0)
            max_t = max(abs(v) for v in raw.values()) or 1.0
            trans = {r: (raw[r] / max_t) * trans_speed for r in DIFF_DRIVE_ROLES}

        rot = {r: 0.0 for r in DIFF_DRIVE_ROLES}
        if abs(rot_speed) > 0.01:
            raw = self.differential_speeds(0, 1.0)
            max_r = max(abs(v) for v in raw.values()) or 1.0
            rot = {r: (raw[r] / max_r) * rot_speed for r in DIFF_DRIVE_ROLES}

        for role in DIFF_DRIVE_ROLES:
            vel = trans[role] + rot[role]
            vel = max(-self.max_vel, min(self.max_vel, vel))
            self.set_vel(role, vel)

    def _drive_mecanum(self, vx, vy, trans_speed, rot_speed):
        trans = {r: 0.0 for r in ALL_ROLES}
        if trans_speed > 0.01:
            raw = self.mecanum_speeds(vx, vy, 0)
            max_t = max(abs(v) for v in raw.values()) or 1.0
            trans = {r: (raw[r] / max_t) * trans_speed for r in ALL_ROLES}

        rot = {r: 0.0 for r in ALL_ROLES}
        if abs(rot_speed) > 0.01:
            raw = self.mecanum_speeds(0, 0, 1.0)
            max_r = max(abs(v) for v in raw.values()) or 1.0
            rot = {r: (raw[r] / max_r) * rot_speed for r in ALL_ROLES}

        for role in ALL_ROLES:
            vel = trans[role] + rot[role]
            vel = max(-self.max_vel, min(self.max_vel, vel))
            self.set_vel(role, vel)

    # ── Status ──────────────────────────────────────────────────────
    def status_line(self):
        parts = []
        for role in sorted(self.active_roles):
            pos = self.positions.get(role, 0)
            amps = self.currents.get(role, 0)
            parts.append(f"{role}:{pos:+.2f}t {amps:.2f}A")
        return "  ".join(parts)
