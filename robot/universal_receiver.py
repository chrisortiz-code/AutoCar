"""
Universal UDP receiver -> USB ODrive motor control (runs on Jetson).
Listens for drive packets from PS4, camera tracking, GUI, or any sender using
the shared D/S/E/Q packet format.

Usage:  python universal_receiver.py [--diff-drive]
"""

import argparse
import json
import signal
import socket
import struct
import threading
import time

from usb_odrive import MecanumUSB, ROLE_DIR, ALL_ROLES

LISTEN_PORT = 5555
RELAY_PORT = 5556
TIMEOUT = 0.5      # no packet for this long -> safety stop
LOG_INTERVAL = 0.2 # seconds between amperage/status prints
CURRENT_SPIKE_THRESHOLD = 8.0  # amps above the average of other motors = stall
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


def _status_responder(mc, driving_ref):
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
                "driving": driving_ref(),
                "uptime": round(time.time() - _start_time, 1),
            }
            try:
                sock.sendto(json.dumps(status).encode(), addr)
            except OSError:
                pass

    sock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff-drive", action="store_true",
                        help="Differential drive mode: back two motors only (BL, BR)")
    args = parser.parse_args()

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
    mc.arm_all()

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
        target=_status_responder, args=(mc, _driving_ref), daemon=True,
    ).start()

    print(f"\nListening on UDP :{LISTEN_PORT}")
    print("Waiting for controller packets from PS4/camera/GUI...\n")

    last_log = 0.0

    try:
        while not _shutdown_event.is_set():
            try:
                data, addr = sock.recvfrom(64)
            except socket.timeout:
                if driving:
                    print("Timeout - no packets. Stopping.")
                    mc.zero_vel()
                    driving = False
                continue

            cmd = chr(data[0])

            if cmd == "D":
                vx, vy, trans_speed, rot_speed = struct.unpack("<ffff", data[1:17])
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

            elif cmd == "E":
                print("\nE-STOP received!")
                mc.stop_all()
                driving = False
                time.sleep(0.3)
                mc.arm_all()
                print("Re-armed.\n")

            elif cmd == "W":
                # Per-wheel velocity: 4 floats in role order (BL,FL,BR,FR)
                vels = struct.unpack("<ffff", data[1:17])
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

            elif cmd == "Q":
                print("Quit received.")
                break

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        mc.shutdown()
        sock.close()


if __name__ == "__main__":
    main()
