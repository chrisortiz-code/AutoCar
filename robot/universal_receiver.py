"""
Universal UDP receiver -> USB ODrive motor control (runs on Jetson).
Listens for drive packets from PS4, camera tracking, GUI, or any sender using
the shared D/S/E/Q packet format.

Usage:  python universal_receiver.py [--diff-drive] [--no-safety]
"""

import argparse
import json
import signal
import socket
import struct
import threading
import time

from motor_control import MecanumUSB, ROLE_DIR, ALL_ROLES
from safety_filter import SafetyFilter
from health_monitor import HealthMonitor, set_oom_priority

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
                "vbus": {r: mc.vbus_voltage.get(r, 0.0) for r in ALL_ROLES},
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
    args = parser.parse_args()

    set_oom_priority()

    if args.diff_drive:
        print("=== DIFFERENTIAL DRIVE MODE (BL + BR only) ===\n")

    mc = MecanumUSB(current_limit=30.0, diff_drive=args.diff_drive)

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
