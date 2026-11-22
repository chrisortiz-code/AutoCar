"""
Universal UDP receiver -> ODrive motor control (runs on Jetson).
Auto-detects CAN (gs_usb) or USB transport.
Listens for drive packets from PS4, camera tracking, GUI, or any sender using
the shared D/S/E/Q packet format.

Usage:  python universal_receiver.py [--diff-drive] [--no-safety] [--transport can|usb|auto]
"""

import argparse
import json
import signal
import socket
import struct
import subprocess
import threading
import time

from safety_filter import SafetyFilter
from health_monitor import HealthMonitor, set_oom_priority

# ── Role constants (shared by both transports) ─────────────────────
ALL_ROLES = ["FL", "FR", "BL", "BR"]
ROLE_DIR = {"FL": -1, "FR": 1, "BL": -1, "BR": 1}


def _can_available():
    """Check if any CAN adapter is present (gs_usb or slcan serial)."""
    try:
        result = subprocess.run(
            ["lsusb"], capture_output=True, text=True, timeout=5,
        )
        if "1d50:606f" in result.stdout:
            return True
    except Exception:
        pass
    import glob
    if glob.glob("/dev/ttyUSB*") or glob.glob("/dev/ttyACM*"):
        return True
    return False


def _socketcan_interface():
    """Return the SocketCAN interface name for the gs_usb adapter, or None.

    Skips the Jetson's native mttcan (can0) and looks for the gs_usb adapter
    interface. Brings it up at 1Mbps if needed.
    """
    try:
        result = subprocess.run(
            ["ip", "-details", "link", "show", "type", "can"],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if ": <" in line and "mtu" in line:
                iface = line.split(":")[1].strip().split("@")[0]
                details = " ".join(lines[i:i+10])
                if "gs_usb" in details:
                    state_result = subprocess.run(
                        ["ip", "-brief", "link", "show", iface],
                        capture_output=True, text=True, timeout=5,
                    )
                    state = state_result.stdout.split()[1] if state_result.stdout.strip() else ""
                    if state != "UP":
                        subprocess.run(
                            ["sudo", "ip", "link", "set", iface, "type", "can",
                             "bitrate", "1000000"],
                            capture_output=True, timeout=5,
                        )
                        subprocess.run(
                            ["sudo", "ip", "link", "set", iface, "up"],
                            capture_output=True, timeout=5,
                        )
                    return iface
            i += 1
    except Exception:
        pass
    return None


def _usb_available():
    """Check if ODrive USB devices are present."""
    try:
        import odrive
        devices = odrive.find_sync(count=1, timeout=3)
        return bool(devices)
    except Exception:
        return False


class CANAdapter:
    """Wraps MecanumCAN to present the same role-string API as MecanumUSB."""

    _NID_TO_ROLE = {0: "BL", 1: "FL", 2: "BR", 3: "FR"}
    _ROLE_TO_NID = {v: k for k, v in _NID_TO_ROLE.items()}

    def __init__(self, **kwargs):
        from can_bus import MecanumCAN
        self._mc = MecanumCAN(**kwargs)
        self.odrives = None

    def _r2n(self, role):
        return self._ROLE_TO_NID[role]

    def _roles(self, nid_set):
        return {self._NID_TO_ROLE[n] for n in nid_set if n in self._NID_TO_ROLE}

    # ── Properties that universal_receiver reads ───────────────────
    @property
    def connected(self):
        return self._roles(self._mc.connected)

    @property
    def armed(self):
        return self._roles(self._mc.armed)

    @property
    def active_roles(self):
        return [self._NID_TO_ROLE[n] for n in self._mc.active_ids
                if n in self._NID_TO_ROLE]

    @property
    def positions(self):
        return {self._NID_TO_ROLE[n]: v
                for n, v in self._mc.positions.items() if n in self._NID_TO_ROLE}

    @property
    def currents(self):
        return {self._NID_TO_ROLE[n]: v
                for n, v in self._mc.currents.items() if n in self._NID_TO_ROLE}

    @property
    def errors(self):
        return {self._NID_TO_ROLE[n]: v
                for n, v in self._mc.errors.items() if n in self._NID_TO_ROLE}

    @property
    def axis_states(self):
        return {self._NID_TO_ROLE[n]: v
                for n, v in self._mc.axis_states.items() if n in self._NID_TO_ROLE}

    @property
    def vbus_voltage(self):
        return {r: 0.0 for r in ALL_ROLES}

    # ── Delegated methods ──────────────────────────────────────────
    def on_fault(self, callback):
        def _adapt(node_id, error_code):
            role = self._NID_TO_ROLE.get(node_id)
            if role:
                callback(role, error_code)
        self._mc.on_fault(_adapt)

    def connect(self):
        _reset_can()
        self._mc.connect()

    def shutdown(self):
        self._mc.shutdown()

    def arm_all(self):
        self._mc.arm_all()

    def stop_all(self):
        self._mc.stop_all()

    def zero_vel(self):
        self._mc.zero_vel()

    def estop_and_recover(self):
        return self._mc.estop_and_recover()

    def drive(self, vx, vy, trans_speed, rot_speed):
        self._mc.drive(vx, vy, trans_speed, rot_speed)

    def set_vel(self, role, velocity):
        nid = self._ROLE_TO_NID.get(role)
        if nid is not None:
            self._mc.set_vel(nid, velocity)

    def clear_all_errors(self):
        self._mc.clear_all_errors()

    def status_line(self):
        return self._mc.status_line()


def create_motor_controller(transport="auto", **kwargs):
    """Factory: returns a motor controller with the role-string API.

    Transports:
        auto       — try odrive-can first (gs_usb via odrive library), then
                      fall back to raw CAN, then USB
        odrive-can — use the odrive library over SocketCAN (reliable, like USB)
        can        — raw python-can protocol (legacy)
        usb        — odrive library over USB
    """
    if transport == "auto":
        # Prefer odrive library over SocketCAN — same reliability as USB
        socketcan_iface = _socketcan_interface()
        if socketcan_iface and _can_available():
            transport = "odrive-can"
        elif _can_available():
            transport = "can"
        else:
            transport = "usb"

    if transport == "odrive-can":
        socketcan_iface = _socketcan_interface()
        if not socketcan_iface:
            print("No SocketCAN interface found, falling back to raw CAN")
            transport = "can"
        else:
            print(f"Transport: ODrive library over SocketCAN ({socketcan_iface})")
            from motor_control import MecanumUSB
            return MecanumUSB(can_interface=socketcan_iface, **kwargs)

    if transport == "can":
        print("Transport: CAN (raw python-can)")
        return CANAdapter(**kwargs)
    else:
        print("Transport: USB")
        from motor_control import MecanumUSB
        return MecanumUSB(**kwargs)

LISTEN_PORT = 5555
RELAY_PORT = 5556
TIMEOUT = 0.5      # no packet for this long -> safety stop
IDLE_DISARM = 10.0 # seconds idle before disarming motors (stops hum)
LOG_INTERVAL = 0.2 # seconds between amperage/status prints
CURRENT_SPIKE_THRESHOLD = 20.0  # amps above the average of other motors = stall
CURRENT_CHECK_INTERVAL = 0.15

_shutdown_event = threading.Event()
_start_time = time.time()


def check_current_spike(mc):
    """Return (role, current) of a stalled motor, or None."""
    currents = {r: abs(mc.currents.get(r, 0.0)) for r in mc.connected}
    if len(currents) < 2:
        return None
    for role, amps in currents.items():
        others = [v for k, v in currents.items() if k != role]
        avg_others = sum(others) / len(others)
        if amps > avg_others + CURRENT_SPIKE_THRESHOLD and amps > 5.0:
            return role, amps
    return None


def _status_responder(mc, driving_ref, safety_ref=None, health_ref=None):
    """Thread: listen on RELAY_PORT, reply to b'?' with JSON motor state."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", RELAY_PORT))
    sock.settimeout(1.0)
    print(f"Status responder on UDP :{RELAY_PORT}")

    while not _shutdown_event.is_set():
        try:
            data, addr = sock.recvfrom(64)
        except socket.timeout:
            continue
        except OSError:
            break

        if data == b"?":
            status = {
                "connected": sorted(mc.connected),
                "armed": sorted(mc.armed),
                "errors": {r: mc.errors.get(r, 0) for r in ALL_ROLES},
                "axis_states": {r: mc.axis_states.get(r, 0) for r in ALL_ROLES},
                "positions": {r: mc.positions.get(r, 0.0) for r in ALL_ROLES},
                "currents": {r: mc.currents.get(r, 0.0) for r in ALL_ROLES},
                "vel_setpoints": {r: getattr(mc, 'vel_setpoints', {}).get(r, 0.0) for r in ALL_ROLES},
                "vbus": {r: getattr(mc, 'vbus_voltage', {}).get(r, 0.0) for r in ALL_ROLES},
                "driving": driving_ref(),
                "uptime": round(time.time() - _start_time, 1),
            }
            if safety_ref:
                s = safety_ref
                status["safety"] = {
                    "enabled": s.enabled,
                    "lidar_connected": s.lidar_connected,
                    "last_action": s.last_action,
                    "nearest_mm": s.nearest_obstacle_mm,
                }
            if health_ref:
                status["health"] = health_ref.status_dict()
            try:
                sock.sendto(json.dumps(status).encode(), addr)
            except OSError:
                pass

    sock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff-drive", action="store_true",
                        help="Differential drive mode: back two motors only (BL, BR)")
    parser.add_argument("--no-safety", action="store_true",
                        help="Disable lidar safety filter on startup")
    parser.add_argument("--safety-min-dist", type=int, default=250,
                        help="Safety filter min distance in mm (default 250)")
    parser.add_argument("--safety-slow-dist", type=int, default=800,
                        help="Safety filter slow distance in mm (default 800)")
    parser.add_argument("--transport", choices=["can", "odrive-can", "usb", "auto"],
                        default="auto",
                        help="Motor transport: odrive-can (recommended), can (raw), usb, or auto-detect")
    args = parser.parse_args()

    set_oom_priority()

    if args.diff_drive:
        print("=== DIFFERENTIAL DRIVE MODE (BL + BR only) ===\n")

    mc = create_motor_controller(
        transport=args.transport,
        current_limit=30.0,
        diff_drive=args.diff_drive,
    )

    recovery_lock = threading.Lock()

    def handle_fault(role, error_code):
        if recovery_lock.locked():
            return
        with recovery_lock:
            print(f"\n!! FAULT on motor {role}, error=0x{error_code:08X}")
            print("   Auto-recovering: stop -> clear errors -> re-arm ...")
            mc.estop_and_recover()
            print("   Recovery complete.\n")

    mc.on_fault(handle_fault)
    mc.connect()

    safety = SafetyFilter(
        enabled=not args.no_safety,
        min_dist_mm=args.safety_min_dist,
        slow_dist_mm=args.safety_slow_dist,
    )
    safety.start()

    health = HealthMonitor(mc=mc)
    health.on_critical(lambda: mc.stop_all())
    health.start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    sock.settimeout(TIMEOUT)

    def signal_handler(sig, frame):
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Start status responder thread
    driving = False

    def _driving_ref():
        return driving

    threading.Thread(
        target=_status_responder, args=(mc, _driving_ref, safety, health),
        daemon=True,
    ).start()

    print(f"\nListening on UDP :{LISTEN_PORT}")
    print("Waiting for controller packets from PS4/camera/GUI...\n")

    last_log = 0.0
    last_drive_time = 0.0

    try:
        while not _shutdown_event.is_set():
            # Disarm after idle to stop motor hum
            if not driving and mc.armed and (time.time() - last_drive_time > IDLE_DISARM):
                print("Idle timeout — disarming motors.")
                mc.stop_all()

            try:
                data, addr = sock.recvfrom(64)
            except socket.timeout:
                if driving:
                    print("Timeout - no packets. Stopping.")
                    mc.zero_vel()
                    driving = False
                    last_drive_time = time.time()
                continue

            cmd = chr(data[0])

            if cmd == "D":
                vx, vy, trans_speed, rot_speed = struct.unpack("<ffff", data[1:17])
                vx, vy, trans_speed, rot_speed, action = safety.filter_drive(
                    vx, vy, trans_speed, rot_speed)
                trans_speed *= health.speed_scale
                rot_speed *= health.speed_scale
                if not mc.armed:
                    mc.arm_all()
                mc.drive(vx, vy, trans_speed, -rot_speed)
                if not driving:
                    print(f"Driving (from {addr[0]})")
                driving = True

                now = time.time()
                if now - last_log >= LOG_INTERVAL:
                    time.sleep(CURRENT_CHECK_INTERVAL)
                    spike = check_current_spike(mc)
                    if spike:
                        role, amps = spike
                        print(f"\n!! CURRENT SPIKE on {role} ({amps:.1f}A) - auto estop+recover")
                        mc.estop_and_recover()
                        driving = False
                        continue
                    print(f"  {mc.status_line()}", end="\r")
                    last_log = now

            elif cmd == "S":
                if driving:
                    mc.zero_vel()
                    print(f"\nStopped. {mc.status_line()}")
                    driving = False
                    last_drive_time = time.time()

            elif cmd == "E":
                print("\nE-STOP received!")
                mc.stop_all()
                driving = False
                time.sleep(0.3)
                mc.arm_all()
                print("Re-armed.\n")

            elif cmd == "W":
                # Per-wheel velocity: 4 floats in role order (BL,FL,BR,FR)
                vels = list(struct.unpack("<ffff", data[1:17]))
                # Approximate net direction from wheel vels for safety check
                net_speed = sum(abs(v) for v in vels) / max(len(vels), 1)
                if net_speed > 0.01:
                    net_vx = (vels[1] + vels[3]) / 2  # FL + FR -> forward
                    net_vy = (vels[0] + vels[1]) / 2 - (vels[2] + vels[3]) / 2
                    _, _, _, _, action = safety.filter_drive(
                        net_vx, net_vy, net_speed, 0.0)
                    if action == "block":
                        vels = [0.0, 0.0, 0.0, 0.0]
                    elif action.startswith("deflect:"):
                        scale = float(action.split(":")[1])
                        vels = [v * scale for v in vels]
                hs = health.speed_scale
                if hs < 1.0:
                    vels = [v * hs for v in vels]
                if not mc.armed:
                    mc.arm_all()
                for role, vel in zip(ALL_ROLES, vels):
                    if role in mc.active_roles:
                        mc.set_vel(role, vel)
                if not driving:
                    print(f"Driving per-wheel (from {addr[0]})")
                driving = True

                now = time.time()
                if now - last_log >= LOG_INTERVAL:
                    time.sleep(CURRENT_CHECK_INTERVAL)
                    spike = check_current_spike(mc)
                    if spike:
                        role, amps = spike
                        print(f"\n!! CURRENT SPIKE on {role} ({amps:.1f}A) - auto estop+recover")
                        mc.estop_and_recover()
                        driving = False
                        continue
                    print(f"  {mc.status_line()}", end="\r")
                    last_log = now

            elif cmd == "T":
                safety.enabled = bool(data[1])
                print(f"Safety {'enabled' if safety.enabled else 'disabled'}")

            elif cmd == "C":
                min_d, slow_d, fov = struct.unpack("<iif", data[1:13])
                safety.min_dist_mm = min_d
                safety.slow_dist_mm = slow_d
                safety.fov_half_deg = fov
                print(f"Safety config: min={min_d}mm slow={slow_d}mm fov={fov:.0f}deg")

            elif cmd == "Q":
                print("Quit received.")
                break

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        health.stop()
        safety.stop()
        mc.shutdown()
        sock.close()


if __name__ == "__main__":
    main()
