"""
Universal UDP receiver -> CAN motor control (runs on Jetson).
Listens for drive packets from PS4, camera tracking, GUI, or any sender using
the shared D/S/E/Q packet format.

Usage:  python universal_receiver.py
"""

import socket
import struct
import time

from can_bus import MecanumCAN

LISTEN_PORT = 5555
TIMEOUT = 0.5      # no packet for this long -> safety stop
LOG_INTERVAL = 0.2 # seconds between amperage/status prints


def main():
    mc = MecanumCAN(current_limit=30.0)
    mc.connect()
    mc.arm_all()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    sock.settimeout(TIMEOUT)

    print(f"\nListening on UDP :{LISTEN_PORT}")
    print("Waiting for controller packets from PS4/camera/GUI...\n")

    driving = False
    last_log = 0.0

    try:
        while True:
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
                mc.drive(vx, vy, trans_speed, rot_speed)
                if not driving:
                    print(f"Driving (from {addr[0]})")
                driving = True

                now = time.time()
                if now - last_log >= LOG_INTERVAL:
                    mc.request_all_iq()
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
