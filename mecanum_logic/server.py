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

RAMP_TIME = 0.3
RAMP_STEPS = 10
ROT_RATE = 90.0           # degrees per second of robot rotation
ROT_WHEEL_SPEED = 6.0     # wheel turns/s during rotation — tune until 180deg is accurate

def _command_all(trans_speeds, trans_scale, rot_speeds, rot_scale):
    """Command each motor with translation + rotation velocities summed."""
    for nid in ALL_IDS:
        if nid not in connected:
            continue
        v = trans_speeds.get(nid, 0) * trans_scale + rot_speeds.get(nid, 0) * rot_scale
        actual = v * MOTORS[nid]["dir"]
        send_can(nid, CMD_SET_INPUT_VEL, struct.pack('<ff', actual, 0.0))

def _run_move(trans_speeds, trans_vel, rot_speeds, rot_vel, trans_dur, rot_dur):
    global moving
    total_dur = max(trans_dur, rot_dur)
    if total_dur <= 0:
        moving = False
        return

    # Arm all motors
    for nid in ALL_IDS:
        if nid in connected:
            arm(nid)

    # Ramp up
    step_dt = RAMP_TIME / RAMP_STEPS
    for i in range(1, RAMP_STEPS + 1):
        frac = i / RAMP_STEPS
        t_s = trans_vel * frac if trans_dur > 0 else 0
        r_s = rot_vel * frac if rot_dur > 0 else 0
        _command_all(trans_speeds, t_s, rot_speeds, r_s)
        time.sleep(step_dt)

    # Main loop: update at 20Hz, drop each component when its time is up
    elapsed = RAMP_TIME
    tick = 0
    while elapsed < total_dur - RAMP_TIME:
        dt = 0.05
        time.sleep(dt)
        elapsed += dt

        t_on = elapsed < trans_dur - RAMP_TIME if trans_dur > 0 else False
        r_on = elapsed < rot_dur - RAMP_TIME if rot_dur > 0 else False

        t_s = trans_vel if t_on else 0
        r_s = rot_vel if r_on else 0
        _command_all(trans_speeds, t_s, rot_speeds, r_s)

        tick += 1
        if tick % 20 == 0:  # print current every ~1s
            cur_str = "  ".join(f"{nid}({MOTORS[nid]['role']}):{currents[nid]:+.3f}A" for nid in sorted(ALL_IDS))
            print(f"  [{elapsed:.1f}/{total_dur:.1f}s] {cur_str}")

    # Ramp down whatever is still active
    for i in range(RAMP_STEPS - 1, -1, -1):
        frac = i / RAMP_STEPS
        t_s = trans_vel * frac if elapsed < trans_dur else 0
        r_s = rot_vel * frac if elapsed < rot_dur else 0
        _command_all(trans_speeds, t_s, rot_speeds, r_s)
        time.sleep(step_dt)

    for nid in ALL_IDS:
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
    magnitude = float(data.get("r", 0))        # distance in turns
    theta_deg = float(data.get("theta", 0))     # direction (0=forward, CW)
    omega_deg = float(data.get("omega", 0))     # total rotation in degrees
    vel_pct   = float(data.get("vel_pct", 30))  # translation speed %

    if magnitude <= 0 and abs(omega_deg) < 0.1:
        return jsonify({"ok": False, "error": "need magnitude or rotation"})

    # --- Translation ---
    trans_vel = (vel_pct / 100.0) * MAX_VEL     # turns/s
    trans_dur = magnitude / trans_vel if magnitude > 0 and trans_vel > 0 else 0

    theta = math.radians(-theta_deg)
    vx = math.cos(theta)
    vy = math.sin(theta)
    trans_speeds = mecanum_speeds(vx, vy, 0)    # pure translation, no rotation

    # --- Rotation ---
    rot_dur = abs(omega_deg) / ROT_RATE if abs(omega_deg) > 0.1 else 0
    rot_sign = 1.0 if omega_deg >= 0 else -1.0
    rot_speeds = mecanum_speeds(0, 0, rot_sign) # pure rotation, unit speeds ±1
    rot_vel = ROT_WHEEL_SPEED                   # direct wheel speed for rotation

    total_dur = max(trans_dur, rot_dur)

    print(f"\nCommand: mag={magnitude:.2f} theta={theta_deg:.1f} rot={omega_deg:.0f}deg")
    print(f"  Translation: {trans_vel:.2f} t/s for {trans_dur:.2f}s")
    print(f"  Rotation:    {rot_vel:.2f} t/s for {rot_dur:.2f}s")
    print(f"  Total:       {total_dur:.2f}s")

    moving = True
    threading.Thread(
        target=_run_move,
        args=(trans_speeds, trans_vel, rot_speeds, rot_vel, trans_dur, rot_dur),
        daemon=True
    ).start()

    return jsonify({
        "ok": True,
        "trans_dur": round(trans_dur, 3),
        "rot_dur": round(rot_dur, 3),
        "total_dur": round(total_dur, 3),
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
