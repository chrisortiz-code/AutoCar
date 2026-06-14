"""
Universal UDP receiver -> CAN motor control (runs on Jetson).
Listens for drive packets from PS4, camera tracking, GUI, or any sender using
the shared D/S/E/Q packet format.

Usage:  python universal_receiver.py
"""

import signal
import socket
import struct
import threading
import time

from can_bus import MecanumCAN

LISTEN_PORT = 5555
TIMEOUT = 0.5      # no packet for this long -> safety stop
LOG_INTERVAL = 0.2 # seconds between amperage/status prints
CURRENT_SPIKE_THRESHOLD = 8.0  # amps above the average of other motors = stall
CURRENT_CHECK_INTERVAL = 0.15

_shutdown_event = threading.Event()


def check_current_spike(mc):
    """Return (node_id, current) of a stalled motor, or None."""
    currents = {nid: abs(mc.currents.get(nid, 0.0)) for nid in mc.connected}
    if len(currents) < 2:
        return None
    for nid, amps in currents.items():
        others = [v for k, v in currents.items() if k != nid]
        avg_others = sum(others) / len(others)
        if amps > avg_others + CURRENT_SPIKE_THRESHOLD and amps > 5.0:
            return nid, amps
    return None


def main():
    mc = MecanumCAN(current_limit=30.0)

    recovery_lock = threading.Lock()

    def handle_fault(node_id, error_code):
        if recovery_lock.locked():
            return
        with recovery_lock:
            from can_bus import MOTORS
            role = MOTORS[node_id]["role"] if node_id in MOTORS else str(node_id)
            print(f"\n!! FAULT on motor {role} (node {node_id}), error=0x{error_code:08X}")
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

    print(f"\nListening on UDP :{LISTEN_PORT}")
    print("Waiting for controller packets from PS4/camera/GUI...\n")

    driving = False
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
                    mc.request_all_iq()
                    time.sleep(CURRENT_CHECK_INTERVAL)
                    spike = check_current_spike(mc)
                    if spike:
                        nid, amps = spike
                        from can_bus import MOTORS
                        role = MOTORS[nid]["role"]
                        print(f"\n!! CURRENT SPIKE on {role} ({amps:.1f}A) - auto estop+recover")
                        mc.estop_and_recover()
                        driving = False
                        continue
                    print(f"  {mc.status_line()}", end="\r")
                    last_log = now

            elif cmd == "S":
                if driving:
                    mc.zero_vel()
                    mc.request_all_iq()
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
                # Per-wheel velocity: 4 floats in node-ID order (0,1,2,3)
                vels = struct.unpack("<ffff", data[1:17])
                for nid, vel in enumerate(vels):
                    mc.set_vel(nid, vel)
                if not driving:
                    print(f"Driving per-wheel (from {addr[0]})")
                driving = True

                now = time.time()
                if now - last_log >= LOG_INTERVAL:
                    mc.request_all_iq()
                    time.sleep(CURRENT_CHECK_INTERVAL)
                    spike = check_current_spike(mc)
                    if spike:
                        nid, amps = spike
                        from can_bus import MOTORS
                        role = MOTORS[nid]["role"]
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
