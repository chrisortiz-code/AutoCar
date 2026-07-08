"""
Path management endpoints — CRUD, execution, recording.
"""

import struct
import socket
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.udp import (
    ALL_IDS, TURNS_PER_DEG, MOTOR_REVS_PER_CM_COMP,
    mecanum_speeds, send_drive, send_wheels, send_stop,
    run_move, run_translate_rotate, UDP_HOST, UDP_PORT, RELAY_PORT,
)

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))
import path_db

router = APIRouter(prefix="/api", tags=["paths"], dependencies=[Depends(require_auth)])

# ── Execution state ───────────────────────────────────────────────────
_state_lock = threading.Lock()
_state = {"status": "idle"}  # idle | executing | recording
_abort = threading.Event()
_recorder = None  # ContinuousRecorder instance


def _set_state(status, **extra):
    with _state_lock:
        _state.clear()
        _state["status"] = status
        _state.update(extra)


def get_state():
    """Return a copy of current state (for sensors router)."""
    with _state_lock:
        return dict(_state)


# ── Sequential execution ─────────────────────────────────────────────

def _execute_sequential(path_id):
    steps = path_db.get_steps(path_id)
    _set_state("executing", path_id=path_id, step=0, total_steps=len(steps))
    _abort.clear()

    try:
        for i, step in enumerate(steps):
            if _abort.is_set():
                break
            with _state_lock:
                _state["step"] = i

            run_translate_rotate(
                step["distance_cm"],
                step["theta_deg"],
                step["rotation_deg"],
                step["vel_pct"],
                abort_event=_abort,
            )

            delay = step.get("delay_ms", 0)
            if delay > 0 and not _abort.is_set():
                import time
                time.sleep(delay / 1000.0)
    finally:
        send_stop()
        _set_state("idle")


def _execute_continuous(path_id):
    import time
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
            send_drive(vx, vy, ts, rs)
            time.sleep(dt)
    finally:
        send_stop()
        _set_state("idle")


# ── Request models ────────────────────────────────────────────────────

class CreatePath(BaseModel):
    name: str = "Untitled"
    path_type: str = "sequential"


class RenamePath(BaseModel):
    name: str


class RecordStart(BaseModel):
    name: str = "Recording"
    sample_rate: int = 20


class RecordSample(BaseModel):
    vx: float = 0
    vy: float = 0
    trans_speed: float = 0
    rot_speed: float = 0


class RecordStop(BaseModel):
    num_coefficients: int = 50


# ── Path CRUD ─────────────────────────────────────────────────────────

@router.get("/paths")
def list_paths():
    return path_db.list_paths()


@router.post("/paths")
def create_path(body: CreatePath):
    pid = path_db.create_path(body.name, body.path_type)
    return {"ok": True, "id": pid}


@router.get("/paths/{pid}")
def get_path(pid: int):
    p = path_db.get_path(pid)
    if not p:
        raise HTTPException(404, "not found")
    if p["path_type"] == "sequential":
        p["steps"] = path_db.get_steps(pid)
    else:
        p["continuous"] = path_db.get_continuous_info(pid)
    return p


@router.patch("/paths/{pid}")
def rename_path(pid: int, body: RenamePath):
    if not body.name:
        raise HTTPException(400, "name required")
    path_db.rename_path(pid, body.name)
    return {"ok": True}


@router.delete("/paths/{pid}")
def delete_path(pid: int):
    path_db.delete_path(pid)
    return {"ok": True}


@router.post("/paths/{pid}/execute")
def execute_path(pid: int):
    with _state_lock:
        if _state["status"] != "idle":
            raise HTTPException(409, f"busy: {_state['status']}")

    p = path_db.get_path(pid)
    if not p:
        raise HTTPException(404, "not found")

    if p["path_type"] == "sequential":
        threading.Thread(target=_execute_sequential, args=(pid,), daemon=True).start()
    else:
        threading.Thread(target=_execute_continuous, args=(pid,), daemon=True).start()

    return {"ok": True, "path_type": p["path_type"]}


@router.post("/paths/{pid}/stop")
def stop_execution(pid: int):
    _abort.set()
    send_stop()
    return {"ok": True}


# ── Recording ─────────────────────────────────────────────────────────

@router.post("/record/start")
def record_start(body: RecordStart):
    global _recorder
    with _state_lock:
        if _state["status"] != "idle":
            raise HTTPException(409, f"busy: {_state['status']}")

    pid = path_db.create_path(body.name, "continuous")
    _recorder = path_db.ContinuousRecorder(pid, body.sample_rate)
    _set_state("recording", path_id=pid, samples=0)
    return {"ok": True, "path_id": pid}


@router.post("/record/sample")
def record_sample(body: RecordSample):
    global _recorder
    if _recorder is None:
        raise HTTPException(409, "not recording")

    _recorder.add_sample(body.vx, body.vy, body.trans_speed, body.rot_speed)
    with _state_lock:
        _state["samples"] = _recorder.num_samples
    return {"ok": True, "samples": _recorder.num_samples}


@router.post("/record/stop")
def record_stop(body: RecordStop = RecordStop()):
    global _recorder
    if _recorder is None:
        raise HTTPException(409, "not recording")

    try:
        result = _recorder.finish(body.num_coefficients)
    except ValueError as e:
        path_db.delete_path(_recorder.path_id)
        _recorder = None
        _set_state("idle")
        raise HTTPException(400, str(e))

    pid = _recorder.path_id
    _recorder = None
    _set_state("idle")
    return {"ok": True, "path_id": pid, **result}


# ── Status ────────────────────────────────────────────────────────────

@router.get("/status")
def status():
    with _state_lock:
        return dict(_state)


# ── UDP relay (started as background task) ────────────────────────────

def start_udp_relay():
    """Start background thread that relays controller UDP packets and
    captures samples during recording."""
    threading.Thread(target=_udp_relay, daemon=True).start()


def _udp_relay():
    relay_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    relay_sock.bind(("0.0.0.0", RELAY_PORT))
    relay_sock.settimeout(1.0)
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_dest = (UDP_HOST, UDP_PORT)
    print(f"UDP relay listening on :{RELAY_PORT} -> forwarding to {UDP_HOST}:{UDP_PORT}")

    while True:
        try:
            data, addr = relay_sock.recvfrom(64)
        except socket.timeout:
            continue

        udp_sock.sendto(data, udp_dest)

        if len(data) >= 17 and chr(data[0]) == "D":
            with _state_lock:
                if _state["status"] == "recording" and _recorder is not None:
                    vx, vy, trans_speed, rot_speed = struct.unpack("<ffff", data[1:17])
                    _recorder.add_sample(vx, vy, trans_speed, rot_speed)
                    _state["samples"] = _recorder.num_samples
