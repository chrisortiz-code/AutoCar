import math
import time
import struct
import threading
import can
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
bus = None
armed = set()
connected = set()
positions = {nid: 0.0 for nid in ALL_IDS}
currents = {nid: 0.0 for nid in ALL_IDS}

CMD_HEARTBEAT      = 0x01
CMD_SET_AXIS_STATE = 0x07
CMD_ENCODER_EST    = 0x09
CMD_SET_CTRL_MODE  = 0x0B
CMD_SET_INPUT_VEL  = 0x0D
CMD_SET_LIMITS     = 0x0F
CMD_GET_IQ         = 0x14

def send_can(node_id, cmd_id, data=b''):
    if bus is None:
        return
    msg = can.Message(
        arbitration_id=(node_id << 5) | cmd_id,
        data=data,
        is_extended_id=False,
    )
    for attempt in range(3):
        try:
            bus.send(msg)
            return
        except Exception as e:
            if attempt == 2:
                print(f"CAN send error node {node_id} cmd 0x{cmd_id:02X}: {e}")
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
            elif cmd_id == CMD_GET_IQ and len(msg.data) >= 8:
                iq_setpoint, iq_measured = struct.unpack('<ff', msg.data[:8])
                if node_id in MOTORS:
                    currents[node_id] = round(iq_measured, 3)
        except:
            pass

def connect_can():
    global bus
    try:
        bus = can.Bus(interface='gs_usb', channel=0, bitrate=1000000)
        print("CAN bus connected!")

        # Retry scan until we find at least 1 ODrive
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
            print("  No ODrives yet, retrying...")

        print(f"Connected: {sorted(connected)}")
        if not connected:
            print("WARNING: No ODrives found. Server will start but motors won't work.")
            print("  Check: ODrives powered? CAN wiring? Correct bitrate?")

        threading.Thread(target=can_listener, daemon=True).start()

    except Exception as e:
        print(f"CAN connection failed: {e}")
        bus = None

def arm(node_id):
    if node_id in armed:
        return
    send_can(node_id, CMD_SET_AXIS_STATE, struct.pack('<I', 8))
    time.sleep(0.15)
    send_can(node_id, CMD_SET_CTRL_MODE, struct.pack('<II', 2, 1))
    time.sleep(0.05)
    send_can(node_id, CMD_SET_LIMITS, struct.pack('<ff', MAX_VEL, 5.0))
    time.sleep(0.05)
    armed.add(node_id)

def set_vel(node_id, velocity):
    if node_id not in connected:
        return
    actual = velocity * MOTORS[node_id]["dir"]
    print(f"  node {node_id} ({MOTORS[node_id]['role']}): {actual:+.3f} turns/s")
    send_can(node_id, CMD_SET_INPUT_VEL, struct.pack('<ff', actual, 0.0))

def stop(node_id):
    send_can(node_id, CMD_SET_INPUT_VEL, struct.pack('<ff', 0.0, 0.0))
    time.sleep(0.05)
    send_can(node_id, CMD_SET_AXIS_STATE, struct.pack('<I', 1))
    armed.discard(node_id)

def mecanum_speeds(vx, vy, omega):
    """Returns {node_id: speed} based on each motor's role."""
    roles = {
        "FL": vx - vy - omega,
        "FR": vx + vy + omega,
        "BL": vx + vy - omega,
        "BR": vx - vy + omega,
    }
    return {nid: roles[m["role"]] for nid, m in MOTORS.items()}

moving = False

WHEEL_DIAMETER = 11.75     # cm
GEAR_RATIO = 16.0 / 90.0  # motor:wheel
CM_PER_MOTOR_REV = GEAR_RATIO * math.pi * WHEEL_DIAMETER  # ~6.562 cm
MOTOR_REVS_PER_CM = 1.0 / CM_PER_MOTOR_REV               # ~0.1524

TURNS_PER_DEG = 0.067     # motor turns per degree of robot rotation — tune this
POS_TOLERANCE = 0.1       # turns — how close to target before "done"
MOVE_TIMEOUT = 30         # seconds — safety timeout

def _command_vel(nid, velocity):
    """Command a single motor velocity (with direction flip)."""
    if nid not in connected:
        return
    actual = velocity * MOTORS[nid]["dir"]
    send_can(nid, CMD_SET_INPUT_VEL, struct.pack('<ff', actual, 0.0))

def _run_move(targets, vel_pct):
    """
    targets: {nid: total_turns_to_move} (signed, direction-independent — dir flip in _command_vel)
    vel_pct: speed as percentage of MAX_VEL
    """
    global moving
    if not targets:
        moving = False
        return

    vel_scale = (vel_pct / 100.0) * MAX_VEL

    # Arm all
    for nid in targets:
        if nid in connected:
            arm(nid)

    # Record start positions
    start_pos = {nid: positions.get(nid, 0.0) for nid in targets}
    goal_pos = {nid: start_pos[nid] + targets[nid] * MOTORS[nid]["dir"] for nid in targets}

    # Compute velocity direction per wheel
    vel_dir = {}
    for nid in targets:
        if abs(targets[nid]) < 0.001:
            vel_dir[nid] = 0.0
        else:
            vel_dir[nid] = vel_scale if targets[nid] > 0 else -vel_scale

    # Command initial velocities
    for nid in targets:
        if nid in connected:
            _command_vel(nid, vel_dir[nid])

    # Monitor encoder positions
    done = {nid: False for nid in targets}
    start_time = time.time()
    tick = 0

    while not all(done.values()):
        time.sleep(0.05)
        tick += 1

        for nid in targets:
            if done[nid]:
                continue
            current = positions.get(nid, 0.0)
            remaining = goal_pos[nid] - current
            if abs(remaining) < POS_TOLERANCE:
                # Reached target — stop this wheel
                _command_vel(nid, 0.0)
                done[nid] = True
                print(f"  node {nid} ({MOTORS[nid]['role']}): reached target ({current:.3f})")
            elif abs(remaining) < 1.0:
                # Close — slow down proportionally
                slow_vel = vel_dir[nid] * (abs(remaining) / 1.0)
                slow_vel = max(abs(slow_vel), 0.5) * (1 if slow_vel >= 0 else -1)
                _command_vel(nid, slow_vel)

        if tick % 20 == 0:
            cur_str = "  ".join(
                f"{nid}({MOTORS[nid]['role']}):{positions.get(nid,0):+.3f}/{goal_pos[nid]:+.3f}"
                for nid in sorted(targets.keys())
            )
            print(f"  {cur_str}")

        if time.time() - start_time > MOVE_TIMEOUT:
            print("  TIMEOUT — stopping all")
            break

    # Stop all
    for nid in targets:
        if nid in connected:
            stop(nid)
    moving = False

@app.route("/status")
def get_status():
    return jsonify({
        "connected": sorted(list(connected)),
        "moving": moving,
        "positions": {nid: positions[nid] for nid in ALL_IDS},
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
    for nid in ALL_IDS:
        stop(nid)
    moving = False
    return jsonify({"ok": True, "message": "All motors idle"})

@app.route("/home", methods=["POST"])
def go_home():
    global moving
    for nid in ALL_IDS:
        stop(nid)
    moving = False
    return jsonify({"ok": True})

@app.route("/wheel_status")
def wheel_status():
    return jsonify({
        nid: {
            "role": MOTORS[nid]["role"],
            "connected": nid in connected,
            "position": positions[nid],
        }
        for nid in ALL_IDS
    })

def shutdown_motors():
    print("\nShutting down...")
    if bus:
        for nid in ALL_IDS:
            try:
                msg = can.Message(
                    arbitration_id=(nid << 5) | CMD_SET_INPUT_VEL,
                    data=struct.pack('<ff', 0.0, 0.0),
                    is_extended_id=False,
                )
                bus.send(msg, timeout=0.1)
                msg = can.Message(
                    arbitration_id=(nid << 5) | CMD_SET_AXIS_STATE,
                    data=struct.pack('<I', 1),
                    is_extended_id=False,
                )
                bus.send(msg, timeout=0.1)
            except:
                pass
        try:
            bus.shutdown()
        except:
            pass
    print("Done.")

if __name__ == "__main__":
    import atexit, signal
    atexit.register(shutdown_motors)
    signal.signal(signal.SIGINT, lambda *_: (shutdown_motors(), exit(0)))
    connect_can()
    print("\nServer running at http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
