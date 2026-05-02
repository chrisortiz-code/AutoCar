"""
PS4 Controller → UDP sender (runs on PC)
Reads controller input, sends drive commands to Jetson over UDP.

Usage:  python ps4_sender.py [JETSON_IP]
        Default IP: 192.168.2.83
"""

import math
import struct
import sys
import time

import pygame

# ── Network config ──────────────────────────────────────────────────
JETSON_IP   = sys.argv[1] if len(sys.argv) > 1 else "192.168.2.83"
JETSON_PORT = 5555

# ── Tuning ──────────────────────────────────────────────────────────
DEADZONE   = 0.12
CYCLE_HZ   = 20
BASE_VEL   = 3.0    # turns/s normal
SPRINT_VEL = 7.0    # turns/s with R2

# PS4 axes / buttons (SDL mapping)
AXIS_LX, AXIS_LY = 0, 1
AXIS_RX           = 2
AXIS_R2           = 5
BTN_X       = 0
BTN_OPTIONS = 6

# ── Packet types ────────────────────────────────────────────────────
# D + 4 floats (vx, vy, omega, top_speed)  = drive
# S                                         = stop (sticks centered)
# E                                         = e-stop
# Q                                         = quit / shutdown

def apply_deadzone(value):
    if abs(value) < DEADZONE:
        return 0.0
    sign = 1.0 if value > 0 else -1.0
    return sign * (abs(value) - DEADZONE) / (1.0 - DEADZONE)


def main():
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (JETSON_IP, JETSON_PORT)

    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print("No controller found! Plug in PS4 controller and retry.")
        return
    js = pygame.joystick.Joystick(0)
    js.init()

    print(f"Controller: {js.get_name()}")
    print(f"Sending to {JETSON_IP}:{JETSON_PORT}")
    print("─" * 50)
    print("  Left stick  = drive (magnitude = speed)")
    print("  R2 trigger  = sprint")
    print("  X button    = e-stop")
    print("  Options     = quit")
    print("─" * 50)

    was_moving = False
    interval = 1.0 / CYCLE_HZ

    try:
        while True:
            t0 = time.time()
            pygame.event.pump()

            # ── E-stop ──
            if js.get_button(BTN_X):
                print("E-STOP!")
                sock.sendto(b'E', dest)
                was_moving = False
                while js.get_button(BTN_X):
                    pygame.event.pump()
                    time.sleep(0.05)
                print("Released. Drive!\n")
                continue

            # ── Quit ──
            if js.get_button(BTN_OPTIONS):
                print("Quit.")
                sock.sendto(b'Q', dest)
                break

            # ── Sticks ──
            lx = apply_deadzone(js.get_axis(AXIS_LX))
            ly = apply_deadzone(js.get_axis(AXIS_LY))
            magnitude = min(1.0, math.hypot(lx, ly))

            # R2: -1 released → +1 pressed → normalize 0–1
            r2 = max(0.0, (js.get_axis(AXIS_R2) + 1.0) / 2.0)
            top_speed = BASE_VEL + r2 * (SPRINT_VEL - BASE_VEL)

            if magnitude > 0.01:
                vx = -ly   # up = forward
                vy = lx    # right = strafe right
                omega = 0.0  # rotation disabled for now

                pkt = b'D' + struct.pack('<ffff', vx, vy, omega, top_speed * magnitude)
                sock.sendto(pkt, dest)

                if not was_moving:
                    print("Driving...")
                was_moving = True

                sprint_tag = " [SPRINT]" if r2 > 0.3 else ""
                print(f"  vx={vx:+.2f} vy={vy:+.2f} mag={magnitude:.2f} spd={top_speed:.1f}{sprint_tag}   ", end="\r")
            else:
                if was_moving:
                    sock.sendto(b'S', dest)
                    print("\nStopped.            ")
                    was_moving = False

            elapsed = time.time() - t0
            if elapsed < interval:
                time.sleep(interval - elapsed)

    except KeyboardInterrupt:
        print("\nInterrupted.")
        sock.sendto(b'E', dest)
    finally:
        pygame.quit()
        sock.close()
        print("Done.")


if __name__ == "__main__":
    main()
