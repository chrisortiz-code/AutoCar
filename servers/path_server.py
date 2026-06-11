"""
path_server.py — Flask REST API for path management, recording, and playback.
Runs on port 5001. Sends UDP packets to universal_receiver.py on port 5555.
"""

import math
import time
import struct
import socket
import threading
import os
import sys
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import path_db

app = Flask(__name__)
CORS(app)

# ── UDP sender (same protocol as flask_gui_server.py) ────────────────
UDP_HOST = os.getenv("ROBOT_IP", "127.0.0.1")
UDP_PORT = int(os.getenv("UDP_PORT", "5555"))
# NOTE: When running on the Jetson, ROBOT_IP can stay as 127.0.0.1 (default).
# Set ROBOT_IP in .env only when running this server on a remote PC.
_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_udp_dest = (UDP_HOST, UDP_PORT)

# ── Motor / kinematic constants (duplicated from flask_gui_server.py) ─
MOTORS = {
    0: {"role": "BL", "dir": -1},
    1: {"role": "FL", "dir": -1},
    2: {"role": "BR", "dir":  1},
    3: {"role": "FR", "dir":  1},
}
ALL_IDS = list(MOTORS.keys())
MAX_VEL = 10.0

WHEEL_DIAMETER = 9.55
GEAR_RATIO = 16.0 / 90.0
CM_PER_MOTOR_REV = GEAR_RATIO * math.pi * WHEEL_DIAMETER
MOTOR_REVS_PER_CM = 1.0 / CM_PER_MOTOR_REV
TURNS_PER_DEG = 0.072
MOVE_TIMEOUT = 30
RAMP_PCT = 0.15
RAMP_MIN = 0.1
_RAMP_AVG = 1.0 - 2 * RAMP_PCT + 2 * RAMP_PCT * (RAMP_MIN + 1.0) / 2
MOTOR_REVS_PER_CM_COMP = MOTOR_REVS_PER_CM / _RAMP_AVG


def _send_drive(vx, vy, trans_speed, rot_speed):
    _udp_sock.sendto(b"D" + struct.pack("<ffff", vx, vy, trans_speed, rot_speed), _udp_dest)


def _send_wheels(wheel_vels):
    data = struct.pack("<ffff", *(wheel_vels.get(nid, 0.0) for nid in range(4)))
    _udp_sock.sendto(b"W" + data, _udp_dest)


def _send_stop():
    _udp_sock.sendto(b"S", _udp_dest)


def mecanum_speeds(vx, vy, omega):
    roles = {
        "FL": vx - vy + omega,
        "FR": vx + vy - omega,
        "BL": vx + vy + omega,
        "BR": vx - vy - omega,
    }
    return {nid: roles[m["role"]] for nid, m in MOTORS.items()}


# ── Execution state ──────────────────────────────────────────────────
_state_lock = threading.Lock()
_state = {"status": "idle"}  # idle | executing | recording
_abort = threading.Event()
_recorder = None  # ContinuousRecorder instance


def _set_state(status, **extra):
    with _state_lock:
        _state.clear()
        _state["status"] = status
        _state.update(extra)


# ── Sequential execution (reuses flask_gui_server logic) ─────────────

def _run_translate_rotate(distance_cm, heading_deg, rotation_deg, vel_pct):
    """Execute a single translate+rotate move. Blocks until done or aborted."""
    magnitude = distance_cm * MOTOR_REVS_PER_CM_COMP
    rot_turns = abs(rotation_deg) * TURNS_PER_DEG
    v = (vel_pct / 100.0) * MAX_VEL

    if v < 0.01:
        return

    # Pure rotation
    if magnitude < 0.01 and rot_turns > 0.01:
        rot_sign = 1.0 if rotation_deg >= 0 else -1.0
        rot_unit = mecanum_speeds(0, 0, rot_sign)
        max_r = max(abs(s) for s in rot_unit.values()) or 1.0
        targets = {nid: rot_unit[nid] / max_r * rot_turns for nid in ALL_IDS}
        _run_move(targets, vel_pct)
        return

    # Pure translation or translate+rotate
    T = max(
        magnitude / v if magnitude > 0.01 else 0,
        rot_turns / v if rot_turns > 0.01 else 0,
    )
    if T < 0.01:
        return

    trans_rate = magnitude / T
    rot_rate = rot_turns / T
    rot_sign = 1.0 if rotation_deg >= 0 else -1.0
    omega_rad_per_s = -math.radians(rotation_deg) / T

    theta_world = math.radians(-heading_deg)
    vx_world = math.cos(theta_world)
    vy_world = math.sin(theta_world)

    rot_unit = mecanum_speeds(0, 0, rot_sign)
    max_r = max(abs(s) for s in rot_unit.values()) or 1.0

    dt = 0.02
    start_time = time.time()
    prev_time = start_time
    ramp_time = T * RAMP_PCT
    theta_accum = 0.0

    try:
        while not _abort.is_set():
            now = time.time()
            elapsed = now - start_time
            dt_actual = now - prev_time
            prev_time = now

            if elapsed >= T or elapsed > MOVE_TIMEOUT:
                break

            remaining = T - elapsed
            if elapsed < ramp_time:
                ramp = RAMP_MIN + (1.0 - RAMP_MIN) * (elapsed / ramp_time)
            elif remaining < ramp_time:
                ramp = RAMP_MIN + (1.0 - RAMP_MIN) * (remaining / ramp_time)
            else:
                ramp = 1.0

            theta_accum += omega_rad_per_s * ramp * dt_actual

            cos_t = math.cos(theta_accum)
            sin_t = math.sin(theta_accum)
            vx_body = cos_t * vx_world + sin_t * vy_world
            vy_body = -sin_t * vx_world + cos_t * vy_world

            trans_unit = mecanum_speeds(vx_body, vy_body, 0)
            max_t = max(abs(s) for s in trans_unit.values()) or 1.0

            wheel_vels = {
                nid: (trans_unit[nid] / max_t) * trans_rate * ramp
                   + (rot_unit[nid] / max_r) * rot_rate * ramp
                for nid in ALL_IDS
            }
            _send_wheels(wheel_vels)
            time.sleep(dt)
    finally:
        _send_stop()


def _run_move(targets, vel_pct):
    """Time-based move with ramp. Blocks until done or aborted."""
    if not targets:
        return
    vel_scale = (vel_pct / 100.0) * MAX_VEL
    max_travel = max(abs(t) for t in targets.values())
    if max_travel < 0.01:
        return

    T = max_travel / vel_scale
    peak = {nid: (targets[nid] / max_travel) * vel_scale for nid in targets}
    ramp_time = T * RAMP_PCT
    dt = 0.02
    start_time = time.time()

    try:
        while not _abort.is_set():
            elapsed = time.time() - start_time
            if elapsed >= T or elapsed > MOVE_TIMEOUT:
                break

            remaining = T - elapsed
            if elapsed < ramp_time:
                ramp = RAMP_MIN + (1.0 - RAMP_MIN) * (elapsed / ramp_time)
            elif remaining < ramp_time:
                ramp = RAMP_MIN + (1.0 - RAMP_MIN) * (remaining / ramp_time)
            else:
                ramp = 1.0

            wheel_vels = {nid: peak[nid] * ramp for nid in targets}
            _send_wheels(wheel_vels)
            time.sleep(dt)
    finally:
        _send_stop()


def _execute_sequential(path_id):
    """Execute all steps of a sequential path."""
    steps = path_db.get_steps(path_id)
    _set_state("executing", path_id=path_id, step=0, total_steps=len(steps))
    _abort.clear()

    try:
        for i, step in enumerate(steps):
            if _abort.is_set():
                break
            with _state_lock:
                _state["step"] = i

            _run_translate_rotate(
                step["distance_cm"],
                step["theta_deg"],
                step["rotation_deg"],
                step["vel_pct"],
            )

            delay = step.get("delay_ms", 0)
            if delay > 0 and not _abort.is_set():
                time.sleep(delay / 1000.0)
    finally:
        _send_stop()
        _set_state("idle")


def _execute_continuous(path_id):
    """Reconstruct and replay a continuous path via D packets."""
    samples = path_db.reconstruct_continuous(path_id)
    if not samples:
        _set_state("idle")
        return

    info = path_db.get_continuous_info(path_id)
    sample_rate = info["sample_rate"]
    dt = 1.0 / sample_rate

    _set_state("executing", path_id=path_id, sample=0, total_samples=len(samples))
    _abort.clear()

    try:
        for i, (vx, vy, ts, rs) in enumerate(samples):
            if _abort.is_set():
                break
            with _state_lock:
                _state["sample"] = i

            _send_drive(vx, vy, ts, rs)
            time.sleep(dt)
    finally:
        _send_stop()
        _set_state("idle")


# ── REST API ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file(os.path.join(os.path.dirname(__file__), "..", "gui", "path_gui.html"))


@app.route("/paths", methods=["GET"])
def list_paths():
    return jsonify(path_db.list_paths())


@app.route("/paths", methods=["POST"])
def create_path():
    data = request.json
    name = data.get("name", "Untitled")
    path_type = data.get("path_type", "sequential")
    pid = path_db.create_path(name, path_type)
    return jsonify({"ok": True, "id": pid})


@app.route("/paths/<int:pid>", methods=["GET"])
def get_path(pid):
    p = path_db.get_path(pid)
    if not p:
        return jsonify({"ok": False, "error": "not found"}), 404
    if p["path_type"] == "sequential":
        p["steps"] = path_db.get_steps(pid)
    else:
        p["continuous"] = path_db.get_continuous_info(pid)
    return jsonify(p)


@app.route("/paths/<int:pid>", methods=["DELETE"])
def delete_path(pid):
    path_db.delete_path(pid)
    return jsonify({"ok": True})


@app.route("/paths/<int:pid>/steps", methods=["GET"])
def get_steps(pid):
    return jsonify(path_db.get_steps(pid))


@app.route("/paths/<int:pid>/steps", methods=["POST"])
def set_steps(pid):
    steps = request.json
    if not isinstance(steps, list):
        return jsonify({"ok": False, "error": "expected list of steps"}), 400
    path_db.set_steps(pid, steps)
    return jsonify({"ok": True})


@app.route("/paths/<int:pid>/execute", methods=["POST"])
def execute_path(pid):
    with _state_lock:
        if _state["status"] != "idle":
            return jsonify({"ok": False, "error": f"busy: {_state['status']}"}), 409

    p = path_db.get_path(pid)
    if not p:
        return jsonify({"ok": False, "error": "not found"}), 404

    if p["path_type"] == "sequential":
        threading.Thread(target=_execute_sequential, args=(pid,), daemon=True).start()
    else:
        threading.Thread(target=_execute_continuous, args=(pid,), daemon=True).start()

    return jsonify({"ok": True, "path_type": p["path_type"]})


@app.route("/paths/<int:pid>/stop", methods=["POST"])
def stop_execution(pid):
    _abort.set()
    _send_stop()
    return jsonify({"ok": True})


@app.route("/paths/<int:pid>/preview", methods=["GET"])
def preview_path(pid):
    num_coeffs = request.args.get("num_coefficients", type=int)
    if num_coeffs:
        samples = path_db.recompress_continuous(pid, num_coeffs)
    else:
        samples = path_db.reconstruct_continuous(pid)

    if samples is None:
        return jsonify({"ok": False, "error": "no continuous data"}), 404

    # Return as separate arrays for easier plotting
    vx = [s[0] for s in samples]
    vy = [s[1] for s in samples]
    ts = [s[2] for s in samples]
    rs = [s[3] for s in samples]
    info = path_db.get_continuous_info(pid)
    return jsonify({
        "vx": vx, "vy": vy, "trans_speed": ts, "rot_speed": rs,
        "sample_rate": info["sample_rate"] if info else 20,
        "num_samples": len(samples),
    })


# ── Recording endpoints ──────────────────────────────────────────────

@app.route("/record/start", methods=["POST"])
def record_start():
    global _recorder
    with _state_lock:
        if _state["status"] != "idle":
            return jsonify({"ok": False, "error": f"busy: {_state['status']}"}), 409

    data = request.json or {}
    name = data.get("name", "Recording")
    sample_rate = data.get("sample_rate", 20)

    pid = path_db.create_path(name, "continuous")
    _recorder = path_db.ContinuousRecorder(pid, sample_rate)
    _set_state("recording", path_id=pid, samples=0)
    return jsonify({"ok": True, "path_id": pid})


@app.route("/record/sample", methods=["POST"])
def record_sample():
    global _recorder
    if _recorder is None:
        return jsonify({"ok": False, "error": "not recording"}), 409

    data = request.json
    _recorder.add_sample(
        float(data.get("vx", 0)),
        float(data.get("vy", 0)),
        float(data.get("trans_speed", 0)),
        float(data.get("rot_speed", 0)),
    )
    with _state_lock:
        _state["samples"] = _recorder.num_samples
    return jsonify({"ok": True, "samples": _recorder.num_samples})


@app.route("/record/stop", methods=["POST"])
def record_stop():
    global _recorder
    if _recorder is None:
        return jsonify({"ok": False, "error": "not recording"}), 409

    data = request.json or {}
    num_coefficients = data.get("num_coefficients", 50)

    try:
        result = _recorder.finish(num_coefficients)
    except ValueError as e:
        path_db.delete_path(_recorder.path_id)
        _recorder = None
        _set_state("idle")
        return jsonify({"ok": False, "error": str(e)}), 400

    pid = _recorder.path_id
    _recorder = None
    _set_state("idle")
    return jsonify({"ok": True, "path_id": pid, **result})


@app.route("/record/status", methods=["GET"])
def record_status():
    with _state_lock:
        if _state["status"] == "recording" and _recorder:
            return jsonify({
                "recording": True,
                "path_id": _state.get("path_id"),
                "samples": _recorder.num_samples,
            })
    return jsonify({"recording": False})


@app.route("/status", methods=["GET"])
def status():
    with _state_lock:
        return jsonify(dict(_state))


@app.route("/stop", methods=["POST"])
def stop_all():
    _abort.set()
    _send_stop()
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("\nPath server running at http://localhost:5001")
    print(f"Sending UDP commands to {UDP_HOST}:{UDP_PORT}")
    print("Make sure universal_receiver.py is running!\n")
    app.run(host="0.0.0.0", port=5001, debug=False)
