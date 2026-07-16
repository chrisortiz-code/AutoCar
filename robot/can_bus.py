"""
Shared CAN / ODrive / mecanum motor interface.
Import this from any control system (server, ps4, future controllers).
"""

import math
import struct
import threading
import time
import gc

import can

# ── Motor layout ────────────────────────────────────────────────────
MOTORS = {
    0: {"role": "BL", "dir": -1},
    1: {"role": "FL", "dir": -1},
    2: {"role": "BR", "dir":  1},
    3: {"role": "FR", "dir":  1},
}
ALL_IDS = list(MOTORS.keys())

# ── CAN command IDs ─────────────────────────────────────────────────
CMD_HEARTBEAT      = 0x01
CMD_SET_AXIS_STATE = 0x07
CMD_ENCODER_EST    = 0x09
CMD_SET_CTRL_MODE  = 0x0B
CMD_SET_INPUT_VEL  = 0x0D
CMD_SET_LIMITS     = 0x0F
CMD_GET_IQ         = 0x14

# ── Defaults ────────────────────────────────────────────────────────
MAX_VEL       = 10.0   # turns/s velocity limit
CURRENT_LIMIT = 30.0   # amps
CAN_BITRATE    = 1000000
SCAN_ATTEMPTS  = 5

# ── Drive constants ─────────────────────────────────────────────────
WHEEL_DIAMETER     = 11.75      # cm
GEAR_RATIO         = 16.0 / 90.0
CM_PER_MOTOR_REV   = GEAR_RATIO * math.pi * WHEEL_DIAMETER
MOTOR_REVS_PER_CM  = 1.0 / CM_PER_MOTOR_REV
TURNS_PER_DEG      = 0.067
POS_TOLERANCE      = 0.1
MOVE_TIMEOUT       = 30
RAMP_PCT           = 0.15


DIFF_DRIVE_IDS = [1, 3]  # FL, FR — front two motors only


class MecanumCAN:
    def __init__(self, max_vel=MAX_VEL, current_limit=CURRENT_LIMIT,
                 diff_drive=False):
        self.bus = None
        self.armed = set()
        self.connected = set()
        self.diff_drive = diff_drive
        self.active_ids = DIFF_DRIVE_IDS if diff_drive else ALL_IDS
        self.positions = {nid: 0.0 for nid in ALL_IDS}
        self.currents = {nid: 0.0 for nid in ALL_IDS}
        self.errors = {nid: 0 for nid in ALL_IDS}
        self.axis_states = {nid: 0 for nid in ALL_IDS}
        self.max_vel = max_vel
        self.current_limit = current_limit
        self._closed = True
        self._listener_thread = None
        self._fault_callback = None
        self._last_fault_time = 0.0

    # ── CAN low-level ───────────────────────────────────────────────
    def send(self, node_id, cmd_id, data=b''):
        if self.bus is None:
            return False
        msg = can.Message(
            arbitration_id=(node_id << 5) | cmd_id,
            data=data, is_extended_id=False,
        )
        for attempt in range(3):
            try:
                self.bus.send(msg)
                return True
            except Exception as e:
                if attempt == 2:
                    print(f"CAN send error node {node_id} cmd 0x{cmd_id:02X}: {e}")
                time.sleep(0.05)
        return False

    def _request(self, node_id, cmd_id, dlc=8):
        if self.bus is None:
            return
        try:
            self.bus.send(can.Message(
                arbitration_id=(node_id << 5) | cmd_id,
                is_remote_frame=True,
                is_extended_id=False,
                dlc=dlc,
            ))
        except Exception:
            pass

    def _remember_node(self, node_id):
        if node_id in MOTORS and node_id not in self.connected:
            self.connected.add(node_id)
            print(f"  Found node {node_id} = {MOTORS[node_id]['role']}")

    def _drain_rx(self, duration=0.2):
        if self.bus is None:
            return
        end = time.time() + duration
        while time.time() < end:
            try:
                if self.bus.recv(timeout=0.02) is None:
                    break
            except Exception:
                break

    def _probe_nodes(self):
        for nid in ALL_IDS:
            self._request(nid, CMD_ENCODER_EST, dlc=8)
            self._request(nid, CMD_GET_IQ, dlc=8)

    def _open_bus(self):
        self.bus = can.Bus(interface='gs_usb', channel=0, bitrate=CAN_BITRATE)
        print("CAN bus connected!")
        self._drain_rx()

    def _close_bus(self):
        bus = self.bus
        self.bus = None
        if bus is not None:
            try:
                bus.shutdown()
            except Exception as e:
                print(f"CAN shutdown warning: {e}")
        gc.collect()
        time.sleep(0.3)

    def _scan_for_odrives(self, attempts=SCAN_ATTEMPTS):
        expect_count = len(self.active_ids)
        for attempt in range(attempts):
            print(f"Scanning for ODrives (attempt {attempt+1}/{attempts})...")
            start = time.time()
            while time.time() - start < 3:
                # Re-probe periodically so ODrives that come up late get poked
                self._probe_nodes()
                probe_end = time.time() + 0.5
                while time.time() < probe_end:
                    msg = self.bus.recv(timeout=0.1)
                    if msg:
                        node_id = msg.arbitration_id >> 5
                        cmd_id = msg.arbitration_id & 0x1F
                        # Accept heartbeats OR probe responses
                        if cmd_id in (CMD_HEARTBEAT, CMD_ENCODER_EST, CMD_GET_IQ):
                            self._remember_node(node_id)
                if len(self.connected) >= expect_count:
                    break
            if self.connected:
                break
            print("  No ODrives yet, retrying...")

    def on_fault(self, callback):
        """Register a callback: callback(node_id, error_code) called on fault."""
        self._fault_callback = callback

    def _listener(self):
        while not self._closed and self.bus:
            try:
                msg = self.bus.recv(timeout=1)
                if msg is None:
                    continue
                node_id = msg.arbitration_id >> 5
                cmd_id  = msg.arbitration_id & 0x1F
                if node_id not in MOTORS:
                    continue
                if cmd_id == CMD_HEARTBEAT and len(msg.data) >= 5:
                    axis_error = struct.unpack('<I', msg.data[:4])[0]
                    axis_state = msg.data[4]
                    prev_error = self.errors.get(node_id, 0)
                    self.errors[node_id] = axis_error
                    self.axis_states[node_id] = axis_state
                    if axis_error != 0 and prev_error == 0 and self._fault_callback:
                        now = time.time()
                        if now - self._last_fault_time > 3.0:
                            self._last_fault_time = now
                            self._fault_callback(node_id, axis_error)
                elif cmd_id == CMD_ENCODER_EST and len(msg.data) >= 4:
                    pos = struct.unpack('<f', msg.data[:4])[0]
                    self.positions[node_id] = round(pos, 3)
                elif cmd_id == CMD_GET_IQ and len(msg.data) >= 8:
                    iq_setpoint, iq_measured = struct.unpack('<ff', msg.data[:8])
                    self.currents[node_id] = round(iq_measured, 3)
            except:
                pass

    # ── Connection ──────────────────────────────────────────────────
    def connect(self):
        try:
            self._closed = False
            self.connected.clear()
            self.armed.clear()
            self._open_bus()
            self._scan_for_odrives()
            if not self.connected:
                print("No ODrives found on first scan. Reopening CAN adapter once...")
                self._close_bus()
                self._open_bus()
                self._scan_for_odrives(attempts=3)
            print(f"Connected: {sorted(self.connected)}")
            if not self.connected:
                print("WARNING: No ODrives found.")
            # Clear any pre-existing errors before starting
            if self.connected:
                print("Clearing any pre-existing ODrive errors...")
                self.clear_all_errors()
            self._listener_thread = threading.Thread(target=self._listener, daemon=True)
            self._listener_thread.start()
        except KeyboardInterrupt:
            self.shutdown()
            raise
        except Exception as e:
            print(f"CAN connection failed: {e}")
            self._closed = True
            self._close_bus()

    def shutdown(self):
        if self._closed and self.bus is None:
            return

        print("Shutting down...")
        self._closed = True

        # Stop listener first so it doesn't compete for the bus
        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=2.0)
        self._listener_thread = None

        # Now send stop commands with bus to ourselves
        if self.bus is not None:
            for nid in list(self.armed):
                try:
                    self.send(nid, CMD_SET_INPUT_VEL, struct.pack('<ff', 0.0, 0.0))
                except Exception:
                    pass
            time.sleep(0.05)
            for nid in list(self.armed):
                try:
                    self.send(nid, CMD_SET_AXIS_STATE, struct.pack('<I', 1))
                except Exception:
                    pass

        self._close_bus()
        self.armed.clear()
        self.connected.clear()
        print("CAN shutdown complete.")

    # ── Motor control ───────────────────────────────────────────────
    def clear_errors(self, node_id):
        """Send clear-errors command (0x18) to an ODrive node."""
        self.send(node_id, 0x18, b'\x00\x00\x00\x00')
        self.errors[node_id] = 0
        time.sleep(0.1)

    def clear_all_errors(self):
        for nid in self.connected:
            self.clear_errors(nid)

    def estop_and_recover(self):
        """Stop all motors, clear errors, and re-arm. Returns False if bus is dead."""
        if self.bus is None:
            print("[recovery] CAN bus is gone, cannot recover")
            return False
        ok = True
        for nid in list(self.armed):
            if not self.send(nid, CMD_SET_INPUT_VEL, struct.pack('<ff', 0.0, 0.0)):
                ok = False
        if not ok:
            print("[recovery] CAN bus not responding, aborting recovery")
            return False
        time.sleep(0.1)
        for nid in list(self.armed):
            self.send(nid, CMD_SET_AXIS_STATE, struct.pack('<I', 1))
        self.armed.clear()
        time.sleep(0.3)
        self.clear_all_errors()
        time.sleep(0.2)
        self.arm_all()
        return True

    def arm(self, node_id):
        if node_id in self.armed:
            return
        self.send(node_id, CMD_SET_AXIS_STATE, struct.pack('<I', 8))
        time.sleep(0.15)
        self.send(node_id, CMD_SET_CTRL_MODE, struct.pack('<II', 2, 1))
        time.sleep(0.05)
        self.send(node_id, CMD_SET_LIMITS,
                  struct.pack('<ff', self.max_vel, self.current_limit))
        time.sleep(0.05)
        self.armed.add(node_id)

    def arm_all(self):
        for nid in self.connected:
            if nid in self.active_ids:
                self.arm(nid)

    def set_vel(self, node_id, velocity):
        if node_id not in self.connected:
            return
        actual = velocity * MOTORS[node_id]["dir"]
        self.send(node_id, CMD_SET_INPUT_VEL, struct.pack('<ff', actual, 0.0))

    def stop_motor(self, node_id):
        self.send(node_id, CMD_SET_INPUT_VEL, struct.pack('<ff', 0.0, 0.0))
        time.sleep(0.05)
        self.send(node_id, CMD_SET_AXIS_STATE, struct.pack('<I', 1))
        self.armed.discard(node_id)

    def stop_all(self):
        for nid in ALL_IDS:
            if nid in self.connected:
                self.stop_motor(nid)

    def zero_vel(self):
        """Set all velocities to zero without disarming."""
        for nid in self.active_ids:
            if nid in self.connected:
                self.set_vel(nid, 0.0)

    def request_iq(self, nid):
        if self.bus is None or nid not in self.connected:
            return
        try:
            self.bus.send(can.Message(
                arbitration_id=(nid << 5) | CMD_GET_IQ,
                is_remote_frame=True, is_extended_id=False, dlc=8,
            ))
        except:
            pass

    def request_all_iq(self):
        for nid in self.connected:
            self.request_iq(nid)

    # ── Mecanum kinematics ──────────────────────────────────────────
    @staticmethod
    def mecanum_speeds(vx, vy, omega):
        roles = {
            "FL": vx - vy - omega,
            "FR": vx + vy + omega,
            "BL": vx + vy - omega,
            "BR": vx - vy + omega,
        }
        return {nid: roles[m["role"]] for nid, m in MOTORS.items()}

    @staticmethod
    def differential_speeds(vx, omega):
        """Back-two-wheel differential drive. vy is ignored.
        BL(0) = vx + omega, BR(2) = vx - omega."""
        return {0: vx + omega, 2: vx - omega}

    def drive(self, vx, vy, trans_speed, rot_speed):
        """Translate + rotate independently, then add per wheel."""
        if self.diff_drive:
            self._drive_diff(vx, trans_speed, rot_speed)
        else:
            self._drive_mecanum(vx, vy, trans_speed, rot_speed)

    def _drive_diff(self, vx, trans_speed, rot_speed):
        """Differential drive: forward/back + rotation on BL/BR only."""
        trans = {0: 0.0, 2: 0.0}
        if trans_speed > 0.01:
            raw = self.differential_speeds(vx, 0)
            max_t = max(abs(v) for v in raw.values()) or 1.0
            trans = {nid: (raw[nid] / max_t) * trans_speed for nid in DIFF_DRIVE_IDS}

        rot = {0: 0.0, 2: 0.0}
        if abs(rot_speed) > 0.01:
            raw = self.differential_speeds(0, 1.0)
            max_r = max(abs(v) for v in raw.values()) or 1.0
            rot = {nid: (raw[nid] / max_r) * rot_speed for nid in DIFF_DRIVE_IDS}

        for nid in DIFF_DRIVE_IDS:
            vel = trans[nid] + rot[nid]
            vel = max(-self.max_vel, min(self.max_vel, vel))
            self.set_vel(nid, vel)

    def _drive_mecanum(self, vx, vy, trans_speed, rot_speed):
        """Full 4-wheel mecanum drive."""
        # Translation
        trans = {nid: 0.0 for nid in ALL_IDS}
        if trans_speed > 0.01:
            raw = self.mecanum_speeds(vx, vy, 0)
            max_t = max(abs(v) for v in raw.values()) or 1.0
            trans = {nid: (raw[nid] / max_t) * trans_speed for nid in ALL_IDS}

        # Rotation
        rot = {nid: 0.0 for nid in ALL_IDS}
        if abs(rot_speed) > 0.01:
            raw = self.mecanum_speeds(0, 0, 1.0)
            max_r = max(abs(v) for v in raw.values()) or 1.0
            rot = {nid: (raw[nid] / max_r) * rot_speed for nid in ALL_IDS}

        # Sum and clamp
        for nid in ALL_IDS:
            vel = trans[nid] + rot[nid]
            vel = max(-self.max_vel, min(self.max_vel, vel))
            self.set_vel(nid, vel)

    # ── Status ──────────────────────────────────────────────────────
    def status_line(self):
        """One-line status: position and current per motor."""
        parts = []
        for nid in sorted(self.active_ids):
            role = MOTORS[nid]["role"]
            pos = self.positions.get(nid, 0)
            amps = self.currents.get(nid, 0)
            parts.append(f"{role}:{pos:+.2f}t {amps:.2f}A")
        return "  ".join(parts)
