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

# ── Drive constants ─────────────────────────────────────────────────
WHEEL_DIAMETER     = 11.75      # cm
GEAR_RATIO         = 16.0 / 90.0
CM_PER_MOTOR_REV   = GEAR_RATIO * math.pi * WHEEL_DIAMETER
MOTOR_REVS_PER_CM  = 1.0 / CM_PER_MOTOR_REV
TURNS_PER_DEG      = 0.067
POS_TOLERANCE      = 0.1
MOVE_TIMEOUT       = 30
RAMP_PCT           = 0.15


class MecanumCAN:
    def __init__(self, max_vel=MAX_VEL, current_limit=CURRENT_LIMIT):
        self.bus = None
        self.armed = set()
        self.connected = set()
        self.positions = {nid: 0.0 for nid in ALL_IDS}
        self.currents = {nid: 0.0 for nid in ALL_IDS}
        self.max_vel = max_vel
        self.current_limit = current_limit
        self._closed = True
        self._listener_thread = None

    # ── CAN low-level ───────────────────────────────────────────────
    def send(self, node_id, cmd_id, data=b''):
        if self.bus is None:
            return
        msg = can.Message(
            arbitration_id=(node_id << 5) | cmd_id,
            data=data, is_extended_id=False,
        )
        for attempt in range(3):
            try:
                self.bus.send(msg)
                return
            except Exception as e:
                if attempt == 2:
                    print(f"CAN send error node {node_id} cmd 0x{cmd_id:02X}: {e}")
                time.sleep(0.05)

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

    def _listener(self):
        while not self._closed and self.bus:
            try:
                msg = self.bus.recv(timeout=1)
                if msg is None:
                    continue
                node_id = msg.arbitration_id >> 5
                cmd_id  = msg.arbitration_id & 0x1F
                if cmd_id == CMD_ENCODER_EST and len(msg.data) >= 4:
                    pos = struct.unpack('<f', msg.data[:4])[0]
                    if node_id in MOTORS:
                        self.positions[node_id] = round(pos, 3)
                elif cmd_id == CMD_GET_IQ and len(msg.data) >= 8:
                    iq_setpoint, iq_measured = struct.unpack('<ff', msg.data[:8])
                    if node_id in MOTORS:
                        self.currents[node_id] = round(iq_measured, 3)
            except:
                pass

    # ── Connection ──────────────────────────────────────────────────
    def connect(self):
        try:
            self._closed = False
            self.connected.clear()
            self.armed.clear()
            self.bus = can.Bus(interface='gs_usb', channel=0, bitrate=1000000)
            print("CAN bus connected!")
            self._drain_rx()
            for attempt in range(5):
                print(f"Scanning for ODrives (attempt {attempt+1}/5)...")
                self._probe_nodes()
                start = time.time()
                while time.time() - start < 3:
                    msg = self.bus.recv(timeout=0.5)
                    if msg:
                        node_id = msg.arbitration_id >> 5
                        self._remember_node(node_id)
                if self.connected:
                    break
                print("  No ODrives yet, retrying...")
            print(f"Connected: {sorted(self.connected)}")
            if not self.connected:
                print("WARNING: No ODrives found.")
            self._listener_thread = threading.Thread(target=self._listener, daemon=True)
            self._listener_thread.start()
        except KeyboardInterrupt:
            self.shutdown()
            raise
        except Exception as e:
            print(f"CAN connection failed: {e}")
            self._closed = True
            if self.bus:
                try:
                    self.bus.shutdown()
                except:
                    pass
            self.bus = None
            gc.collect()

    def shutdown(self):
        bus = self.bus
        if bus is None:
            self._closed = True
            return

        print("Stopping motors and closing CAN...")
        try:
            self.zero_vel()
        except Exception as e:
            print(f"Motor stop warning: {e}")

        self._closed = True
        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=2.0)
        self._listener_thread = None

        try:
            bus.shutdown()
        except Exception as e:
            print(f"CAN shutdown warning: {e}")

        self.bus = None
        self.armed.clear()
        gc.collect()
        time.sleep(0.2)
        print("Done.")

    # ── Motor control ───────────────────────────────────────────────
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
        for nid in ALL_IDS:
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

    def drive(self, vx, vy, trans_speed, rot_speed):
        """Translate + rotate independently, then add per wheel."""
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
        for nid in sorted(ALL_IDS):
            role = MOTORS[nid]["role"]
            pos = self.positions.get(nid, 0)
            amps = self.currents.get(nid, 0)
            parts.append(f"{role}:{pos:+.2f}t {amps:.2f}A")
        return "  ".join(parts)
