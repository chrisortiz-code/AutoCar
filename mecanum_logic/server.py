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
        print("Listening for ODrives (3s)...")

        start = time.time()
        seen = set()
        while time.time() - start < 3:
            msg = bus.recv(timeout=0.5)
            if msg:
                node_id = msg.arbitration_id >> 5
                if node_id in MOTORS and node_id not in seen:
                    seen.add(node_id)
                    connected.add(node_id)
                    print(f"  Found node {node_id} = {MOTORS[node_id]['role']}")

        print(f"Connected: {sorted(connected)}")
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

def _run_move(speeds, vel_scale, duration):
    global moving
    # Arm all motors first
    for nid in speeds:
        if nid in connected:
            arm(nid)

    # Then command all velocities together
    for nid, spd in speeds.items():
        if nid in connected:
            set_vel(nid, spd * vel_scale)

    # Print current (amps) 10 times during move
    interval = duration / 10.0
    for i in range(10):
        time.sleep(interval)
        cur_str = "  ".join(f"{nid}({MOTORS[nid]['role']}):{currents[nid]:+.3f}A" for nid in sorted(speeds.keys()))
        print(f"  [{i+1}/10] {cur_str}")

    for nid in speeds:
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
    duration  = float(data.get("r", 0))
    theta_deg = float(data.get("theta", 0))
    omega     = float(data.get("omega", 0))
    vel_pct   = float(data.get("vel_pct", 30))

    if duration <= 0:
        return jsonify({"ok": False, "error": "magnitude must be > 0"})

    theta = math.radians(theta_deg)
    vx = math.cos(theta)
    vy = math.sin(theta)

    speeds    = mecanum_speeds(vx, vy, omega)
    vel_scale = (vel_pct / 100.0) * MAX_VEL

    print(f"\nCommand: dur={duration:.1f}s theta={theta_deg} omega={omega:.2f} vel={vel_pct}%")

    moving = True
    threading.Thread(target=_run_move, args=(speeds, vel_scale, duration), daemon=True).start()

    return jsonify({
        "ok": True,
        "speeds": {nid: round(v * vel_scale, 3) for nid, v in speeds.items()},
        "duration": duration,
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
    print("\nSetting all motors to idle...")
    for nid in ALL_IDS:
        try:
            send_can(nid, CMD_SET_INPUT_VEL, struct.pack('<ff', 0.0, 0.0))
            time.sleep(0.02)
            send_can(nid, CMD_SET_AXIS_STATE, struct.pack('<I', 1))
        except:
            pass
    if bus:
        try:
            bus.shutdown()
        except:
            pass
    print("CAN adapter released.")

if __name__ == "__main__":
    import atexit, signal
    atexit.register(shutdown_motors)
    signal.signal(signal.SIGINT, lambda *_: (shutdown_motors(), exit(0)))
    connect_can()
    print("\nServer running at http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=False)
