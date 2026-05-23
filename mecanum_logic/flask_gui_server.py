import math
import time
import struct
import socket
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Motor config: node_id -> (mecanum role, direction multiplier)
# direction: +1 means positive CAN velocity = forward, -1 means negate
MOTORS = {
    0: {"role": "BL", "dir": -1},
    1: {"role": "FL", "dir": -1},
    2: {"role": "BR", "dir":  1},
    3: {"role": "FR", "dir":  1},
}

ALL_IDS = list(MOTORS.keys())
MAX_VEL = 10.0

# ── UDP sender to universal_receiver.py ──────────────────────────────
UDP_HOST = "127.0.0.1"
UDP_PORT = 5555
_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_udp_dest = (UDP_HOST, UDP_PORT)


def _send_drive(vx, vy, trans_speed, rot_speed):
    """Send a 'D' drive packet (mecanum mixing done by receiver)."""
    _udp_sock.sendto(b"D" + struct.pack("<ffff", vx, vy, trans_speed, rot_speed), _udp_dest)


def _send_wheels(wheel_vels):
    """Send a 'W' per-wheel velocity packet. wheel_vels: {nid: vel} in logical space."""
    # Pack in node-ID order: 0, 1, 2, 3
    data = struct.pack("<ffff", *(wheel_vels.get(nid, 0.0) for nid in range(4)))
    _udp_sock.sendto(b"W" + data, _udp_dest)


def _send_stop():
    """Send an 'S' stop packet (zero velocity, stay armed)."""
    _udp_sock.sendto(b"S", _udp_dest)


def _send_estop():
    """Send an 'E' e-stop packet."""
    _udp_sock.sendto(b"E", _udp_dest)


# ── Mecanum math ─────────────────────────────────────────────────────

def mecanum_speeds(vx, vy, omega):
    """Returns {node_id: speed} based on each motor's role."""
    roles = {
        "FL": vx - vy - omega,
        "FR": vx + vy + omega,
        "BL": vx + vy - omega,
        "BR": vx - vy + omega,
    }
    return {nid: roles[m["role"]] for nid, m in MOTORS.items()}


# ── State ────────────────────────────────────────────────────────────

moving = False

WHEEL_DIAMETER = 11.75     # cm
GEAR_RATIO = 16.0 / 90.0  # motor:wheel
CM_PER_MOTOR_REV = GEAR_RATIO * math.pi * WHEEL_DIAMETER  # ~6.562 cm
MOTOR_REVS_PER_CM = 1.0 / CM_PER_MOTOR_REV               # ~0.1524

TURNS_PER_DEG = 0.067     # motor turns per degree of robot rotation — tune this
MOVE_TIMEOUT = 30         # seconds — safety timeout
RAMP_PCT = 0.15           # ramp over first/last 15% of travel time

# ---------------------------------------------------------------------------
#  TIME-BASED MOVE — sends per-wheel velocities via UDP
# ---------------------------------------------------------------------------

def _run_move(targets, vel_pct):
    """
    targets: {nid: total_turns_to_move} (signed, logical space)
    vel_pct: speed as percentage of MAX_VEL

    Time-based move: computes duration from the longest wheel travel,
    ramps velocity up/down, sends per-wheel velocities via UDP.
    """
    global moving
    if not targets:
        moving = False
        return

    vel_scale = (vel_pct / 100.0) * MAX_VEL

    # Master wheel = the one with the most travel
    max_travel = max(abs(t) for t in targets.values())
    if max_travel < 0.01:
        moving = False
        return

    # Total time for the move
    T = max_travel / vel_scale

    # Peak velocity per wheel, scaled so all finish together.
    # ratio = targets[nid] / max_travel gives a signed value in [-1, 1].
    peak = {nid: (targets[nid] / max_travel) * vel_scale for nid in targets}

    ramp_time = T * RAMP_PCT
    dt = 0.02  # 50 Hz
    start_time = time.time()

    print(f"\nMove: max_travel={max_travel:.2f} turns, vel={vel_pct}%, T={T:.2f}s")
    for nid in sorted(targets.keys()):
        print(f"  node {nid} ({MOTORS[nid]['role']}): {targets[nid]:+.2f} turns, peak={peak[nid]:+.3f} t/s")

    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= T:
                break
            if elapsed > MOVE_TIMEOUT:
                print("  TIMEOUT — stopping")
                break

            # Time-based ramp
            remaining = T - elapsed
            if elapsed < ramp_time:
                ramp = 0.1 + 0.9 * (elapsed / ramp_time)
            elif remaining < ramp_time:
                ramp = 0.1 + 0.9 * (remaining / ramp_time)
            else:
                ramp = 1.0

            wheel_vels = {nid: peak[nid] * ramp for nid in targets}
            _send_wheels(wheel_vels)

            time.sleep(dt)
    finally:
        _send_stop()
        moving = False


# ---------------------------------------------------------------------------
#  VELOCITY-BASED TRANSLATE+ROTATE — real-time heading compensation
# ---------------------------------------------------------------------------

def _run_translate_rotate(distance_cm, heading_deg, rotation_deg, vel_pct):
    """Drive a straight world-frame line while simultaneously rotating.

    Uses a 50Hz velocity loop that continuously rotates the body-frame
    velocity vector to compensate for the robot's changing heading.
    """
    global moving

    magnitude = distance_cm * MOTOR_REVS_PER_CM  # total motor revs for translation
    rot_turns = abs(rotation_deg) * TURNS_PER_DEG  # total motor revs for rotation
    v = (vel_pct / 100.0) * MAX_VEL              # cruise speed in turns/s

    if v < 0.01:
        moving = False
        return

    # T must cover the worst-case wheel (translation + rotation in same direction).
    # The normalization step caps max wheel speed at v each tick, so we need
    # enough time for the busiest wheel to complete all its turns.
    max_wheel_travel = magnitude + rot_turns
    T = max_wheel_travel / v  # seconds

    # Rotation rate in motor-turns/s and rad/s
    omega_turns = rotation_deg * TURNS_PER_DEG / T  # signed
    omega_rad_per_s = math.radians(rotation_deg) / T

    # World-frame velocity direction (fixed for entire move)
    theta_world = math.radians(-heading_deg)
    vx_world = math.cos(theta_world)
    vy_world = math.sin(theta_world)

    dt = 0.02  # 50 Hz
    start_time = time.time()
    ramp_time = T * RAMP_PCT  # seconds for ramp-up / ramp-down

    print(f"\nTranslate+Rotate: {distance_cm:.0f}cm heading={heading_deg:.1f} "
          f"rot={rotation_deg:.0f}deg vel={vel_pct}% T={T:.2f}s")

    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= T:
                break
            if elapsed > MOVE_TIMEOUT:
                print("  TIMEOUT — stopping all")
                break

            # Current heading offset (open-loop)
            theta_now = omega_rad_per_s * elapsed

            # Rotate world velocity into body frame
            cos_t = math.cos(theta_now)
            sin_t = math.sin(theta_now)
            vx_body = cos_t * vx_world + sin_t * vy_world
            vy_body = -sin_t * vx_world + cos_t * vy_world

            # Compute raw mecanum wheel speeds (translation + rotation)
            raw = mecanum_speeds(vx_body, vy_body, omega_turns)

            # Normalize so the max wheel matches cruise speed
            max_raw = max(abs(s) for s in raw.values())
            if max_raw < 0.001:
                scale = 0.0
            else:
                scale = v / max_raw

            # Time-based ramp (accel at start, decel at end)
            remaining = T - elapsed
            if elapsed < ramp_time:
                ramp = 0.1 + 0.9 * (elapsed / ramp_time)
            elif remaining < ramp_time:
                ramp = 0.1 + 0.9 * (remaining / ramp_time)
            else:
                ramp = 1.0

            wheel_vels = {nid: raw[nid] * scale * ramp for nid in ALL_IDS}
            _send_wheels(wheel_vels)

            time.sleep(dt)
    finally:
        _send_stop()
        moving = False


@app.route("/move_translate_rotate", methods=["POST"])
def move_translate_rotate():
    global moving
    data = request.json
    distance_cm = float(data.get("r", 0))
    theta_deg = float(data.get("theta", 0))
    omega_deg = float(data.get("omega", 0))
    vel_pct = float(data.get("vel_pct", 30))

    # Pure rotation or pure translation → delegate to existing move_polar logic
    if distance_cm <= 0 or abs(omega_deg) < 0.1:
        return move_polar()

    if moving:
        return jsonify({"ok": False, "error": "already moving"})

    moving = True
    threading.Thread(
        target=_run_translate_rotate,
        args=(distance_cm, theta_deg, omega_deg, vel_pct),
        daemon=True,
    ).start()

    return jsonify({"ok": True, "mode": "translate_rotate",
                    "distance_cm": distance_cm, "rotation_deg": omega_deg})


@app.route("/status")
def get_status():
    return jsonify({
        "connected": sorted(ALL_IDS),
        "moving": moving,
        "position": 0.0,
        "motors": {nid: MOTORS[nid]["role"] for nid in ALL_IDS},
    })


@app.route("/move_polar", methods=["POST"])
def move_polar():
    global moving
    data = request.json
    distance_cm = float(data.get("r", 0))       # distance in cm
    magnitude = distance_cm * MOTOR_REVS_PER_CM  # convert to motor revolutions
    theta_deg = float(data.get("theta", 0))     # direction (0=forward, CW)
    omega_deg = float(data.get("omega", 0))     # total rotation in degrees
    vel_pct   = float(data.get("vel_pct", 30))  # speed %

    if moving:
        return jsonify({"ok": False, "error": "already moving"})

    if magnitude <= 0 and abs(omega_deg) < 0.1:
        return jsonify({"ok": False, "error": "need magnitude or rotation"})

    # --- Translation targets (turns per wheel) ---
    theta = math.radians(-theta_deg)
    vx = math.cos(theta)
    vy = math.sin(theta)
    trans_unit = mecanum_speeds(vx, vy, 0)
    trans_targets = {nid: trans_unit[nid] * magnitude for nid in ALL_IDS}

    # --- Rotation targets (turns per wheel) ---
    rot_turns = abs(omega_deg) * TURNS_PER_DEG
    rot_sign = 1.0 if omega_deg >= 0 else -1.0
    rot_unit = mecanum_speeds(0, 0, rot_sign)
    rot_targets = {nid: rot_unit[nid] * rot_turns for nid in ALL_IDS}

    # --- Combined: each wheel's total turns to move ---
    targets = {nid: trans_targets[nid] + rot_targets[nid] for nid in ALL_IDS}

    print(f"\nCommand: {distance_cm:.0f}cm ({magnitude:.2f} revs) theta={theta_deg:.1f} rot={omega_deg:.0f}deg vel={vel_pct}%")
    for nid in sorted(ALL_IDS):
        print(f"  node {nid} ({MOTORS[nid]['role']}): trans={trans_targets[nid]:+.2f} rot={rot_targets[nid]:+.2f} total={targets[nid]:+.2f} turns")

    moving = True
    threading.Thread(target=_run_move, args=(targets, vel_pct), daemon=True).start()

    return jsonify({
        "ok": True,
        "targets": {nid: round(targets[nid], 3) for nid in ALL_IDS},
    })


@app.route("/stop", methods=["POST"])
@app.route("/estop", methods=["POST"])
def estop():
    global moving
    _send_estop()
    moving = False
    return jsonify({"ok": True, "message": "All motors stopped"})


@app.route("/home", methods=["POST"])
def go_home():
    global moving
    _send_stop()
    moving = False
    return jsonify({"ok": True})


@app.route("/wheel_status")
def wheel_status():
    return jsonify({
        nid: {
            "role": MOTORS[nid]["role"],
        }
        for nid in ALL_IDS
    })


if __name__ == "__main__":
    print("\nServer running at http://localhost:5000")
    print(f"Sending UDP commands to {UDP_HOST}:{UDP_PORT}")
    print("Make sure universal_receiver.py is running!\n")
    app.run(host='0.0.0.0', port=5000, debug=False)
