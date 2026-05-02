"""
UDP receiver → CAN motor control (runs on Jetson)
Listens for drive packets from ps4_sender.py on the PC.

Usage:  python ps4_receiver.py
"""

import math
import socket
import struct
import threading
import time

import can

# ── Motor / CAN config (mirrors server.py) ─────────────────────────
MOTORS = {
    0: {"role": "BL", "dir": -1},
    1: {"role": "FL", "dir": -1},
    2: {"role": "BR", "dir":  1},
    3: {"role": "FR", "dir":  1},
}
ALL_IDS = list(MOTORS.keys())
MAX_VEL = 10.0

CMD_SET_AXIS_STATE = 0x07
CMD_ENCODER_EST    = 0x09
CMD_SET_CTRL_MODE  = 0x0B
CMD_SET_INPUT_VEL  = 0x0D
CMD_SET_LIMITS     = 0x0F

bus = None
armed = set()
connected = set()
positions = {nid: 0.0 for nid in ALL_IDS}

LISTEN_PORT = 5555
TIMEOUT     = 0.5   # if no packet for this long, stop motors (safety)


# ── CAN helpers ─────────────────────────────────────────────────────
def send_can(node_id, cmd_id, data=b''):
    if bus is None:
        return
    msg = can.Message(
        arbitration_id=(node_id << 5) | cmd_id,
        data=data, is_extended_id=False,
    )
    for attempt in range(3):
        try:
            bus.send(msg)
            return
        except Exception as e:
            if attempt == 2:
                print(f"CAN send error node {node_id}: {e}")
            time.sleep(0.05)


def can_listener():
    while bus:
        try:
            msg = bus.recv(timeout=1)
            if msg is None:
                continue
            node_id = msg.arbitration_id >> 5
            cmd_id  = msg.arbitration_id & 0x1F
            if cmd_id == CMD_ENCODER_EST and len(msg.data) >= 4:
                pos = struct.unpack('<f', msg.data[:4])[0]
                if node_id in MOTORS:
                    positions[node_id] = round(pos, 3)
        except:
            pass


def connect_can():
    global bus
    try:
        bus = can.Bus(interface='gs_usb', channel=0, bitrate=1000000)
        print("CAN bus connected!")
        for attempt in range(5):
            print(f"Scanning for ODrives (attempt {attempt+1}/5)...")
            start = time.time()
            while time.time() - start < 3:
                msg = bus.recv(timeout=0.5)
                if msg:
                    node_id = msg.arbitration_id >> 5
                    if node_id in MOTORS and node_id not in connected:
                        connected.add(node_id)
                        print(f"  Found node {node_id} = {MOTORS[node_id]['role']}")
            if connected:
                break
        print(f"Connected: {sorted(connected)}")
        if not connected:
            print("WARNING: No ODrives found.")
        threading.Thread(target=can_listener, daemon=True).start()
    except Exception as e:
        print(f"CAN connection failed: {e}")
        bus = None


def arm_motor(node_id):
    if node_id in armed:
        return
    send_can(node_id, CMD_SET_AXIS_STATE, struct.pack('<I', 8))
    time.sleep(0.15)
    send_can(node_id, CMD_SET_CTRL_MODE, struct.pack('<II', 2, 1))
    time.sleep(0.05)
    send_can(node_id, CMD_SET_LIMITS, struct.pack('<ff', MAX_VEL, 5.0))
    time.sleep(0.05)
    armed.add(node_id)


def command_vel(node_id, velocity):
    if node_id not in connected:
        return
    actual = velocity * MOTORS[node_id]["dir"]
    send_can(node_id, CMD_SET_INPUT_VEL, struct.pack('<ff', actual, 0.0))


def stop_motor(node_id):
    send_can(node_id, CMD_SET_INPUT_VEL, struct.pack('<ff', 0.0, 0.0))
    time.sleep(0.05)
    send_can(node_id, CMD_SET_AXIS_STATE, struct.pack('<I', 1))
    armed.discard(node_id)


def stop_all():
    for nid in ALL_IDS:
        if nid in connected:
            stop_motor(nid)


def arm_all():
    for nid in connected:
        arm_motor(nid)


def mecanum_speeds(vx, vy, omega):
    roles = {
        "FL": vx - vy - omega,
        "FR": vx + vy + omega,
        "BL": vx + vy - omega,
        "BR": vx - vy + omega,
    }
    return {nid: roles[m["role"]] for nid, m in MOTORS.items()}


# ── Drive from packet ───────────────────────────────────────────────
def drive(vx, vy, trans_speed, rot_speed):
    """Set motor velocities. Translation and rotation add independently."""
    # Translation component (left stick)
    trans = {nid: 0.0 for nid in ALL_IDS}
    if trans_speed > 0.01:
        raw = mecanum_speeds(vx, vy, 0)
        max_t = max(abs(v) for v in raw.values()) or 1.0
        trans = {nid: (raw[nid] / max_t) * trans_speed for nid in ALL_IDS}

    # Rotation component (right stick) — normalized independently
    rot = {nid: 0.0 for nid in ALL_IDS}
    if abs(rot_speed) > 0.01:
        raw = mecanum_speeds(0, 0, 1.0)
        max_r = max(abs(v) for v in raw.values()) or 1.0
        rot = {nid: (raw[nid] / max_r) * rot_speed for nid in ALL_IDS}

    # Add and clamp to MAX_VEL
    for nid in ALL_IDS:
        vel = trans[nid] + rot[nid]
        vel = max(-MAX_VEL, min(MAX_VEL, vel))
        command_vel(nid, vel)


# ── Main loop ──────────────────────────────────────────────────────
def main():
    connect_can()
    arm_all()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    sock.settimeout(TIMEOUT)

    print(f"\nListening on UDP :{LISTEN_PORT}")
    print("Waiting for controller packets...\n")

    driving = False
    last_packet = time.time()

    try:
        while True:
            try:
                data, addr = sock.recvfrom(64)
            except socket.timeout:
                # no packet received — safety stop
                if driving:
                    print("Timeout — no packets. Stopping.")
                    for nid in ALL_IDS:
                        command_vel(nid, 0.0)
                    driving = False
                continue

            last_packet = time.time()
            cmd = chr(data[0])

            if cmd == 'D':
                # Drive: 4 floats (vx, vy, trans_speed, rot_speed)
                vx, vy, trans_speed, rot_speed = struct.unpack('<ffff', data[1:17])
                drive(vx, vy, trans_speed, rot_speed)
                if not driving:
                    print(f"Driving (from {addr[0]})")
                driving = True

            elif cmd == 'S':
                # Sticks centered — zero velocity
                if driving:
                    for nid in ALL_IDS:
                        command_vel(nid, 0.0)
                    print("Stopped.")
                    driving = False

            elif cmd == 'E':
                # E-stop — kill and re-arm
                print("E-STOP received!")
                stop_all()
                driving = False
                time.sleep(0.3)
                arm_all()
                print("Re-armed.\n")

            elif cmd == 'Q':
                print("Quit received.")
                break

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        print("Shutting down motors...")
        stop_all()
        sock.close()
        if bus:
            try:
                bus.shutdown()
            except:
                pass
        print("Done.")


if __name__ == "__main__":
    main()
