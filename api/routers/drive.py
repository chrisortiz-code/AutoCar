"""
Motor control endpoints — polar moves, translate+rotate, per-wheel, stop/estop.
"""

import math
import threading

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.udp import (
    ALL_IDS, MOTORS, MOTOR_REVS_PER_CM_COMP, TURNS_PER_DEG,
    mecanum_speeds, send_drive, send_wheels, send_stop, send_estop,
    run_move, run_translate_rotate, query_motor_status,
)

router = APIRouter(prefix="/api/drive", tags=["drive"], dependencies=[Depends(require_auth)])

# ── Shared state ──────────────────────────────────────────────────────
_moving = False
_move_lock = threading.Lock()


def is_moving():
    return _moving


# ── Request models ────────────────────────────────────────────────────

class PolarMove(BaseModel):
    r: float = Field(0, description="Distance in cm")
    theta: float = Field(0, description="Direction in degrees (0=forward, CW)")
    omega: float = Field(0, description="Rotation in degrees")
    vel_pct: float = Field(30, description="Speed as percentage of max")


class TranslateRotateMove(BaseModel):
    r: float = Field(0, description="Distance in cm")
    theta: float = Field(0, description="Heading in degrees")
    omega: float = Field(0, description="Rotation in degrees")
    vel_pct: float = Field(30, description="Speed as percentage of max")


class WheelVelocities(BaseModel):
    wheels: dict[int, float] = Field(
        ..., description="Per-wheel velocities {node_id: velocity}"
    )


# ── Move thread wrappers ─────────────────────────────────────────────

def _do_polar_move(distance_cm, theta_deg, omega_deg, vel_pct):
    global _moving
    magnitude = distance_cm * MOTOR_REVS_PER_CM_COMP
    theta = math.radians(-theta_deg)
    vx = math.cos(theta)
    vy = math.sin(theta)
    trans_unit = mecanum_speeds(vx, vy, 0)
    trans_targets = {nid: trans_unit[nid] * magnitude for nid in ALL_IDS}

    rot_turns = abs(omega_deg) * TURNS_PER_DEG
    rot_sign = 1.0 if omega_deg >= 0 else -1.0
    rot_unit = mecanum_speeds(0, 0, rot_sign)
    rot_targets = {nid: rot_unit[nid] * rot_turns for nid in ALL_IDS}

    targets = {nid: trans_targets[nid] + rot_targets[nid] for nid in ALL_IDS}
    try:
        run_move(targets, vel_pct)
    finally:
        _moving = False


def _do_translate_rotate(distance_cm, heading_deg, rotation_deg, vel_pct):
    global _moving
    try:
        run_translate_rotate(distance_cm, heading_deg, rotation_deg, vel_pct)
    finally:
        _moving = False


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/polar")
def move_polar(body: PolarMove):
    global _moving
    with _move_lock:
        if _moving:
            return {"ok": False, "error": "already moving"}

        magnitude = body.r * MOTOR_REVS_PER_CM_COMP
        if magnitude <= 0 and abs(body.omega) < 0.1:
            return {"ok": False, "error": "need magnitude or rotation"}

        _moving = True

    threading.Thread(
        target=_do_polar_move,
        args=(body.r, body.theta, body.omega, body.vel_pct),
        daemon=True,
    ).start()

    return {"ok": True, "mode": "polar"}


@router.post("/translate-rotate")
def move_translate_rotate(body: TranslateRotateMove):
    global _moving
    with _move_lock:
        if _moving:
            return {"ok": False, "error": "already moving"}

        # Pure rotation or pure translation → delegate to polar logic
        if body.r <= 0 or abs(body.omega) < 0.1:
            magnitude = body.r * MOTOR_REVS_PER_CM_COMP
            if magnitude <= 0 and abs(body.omega) < 0.1:
                return {"ok": False, "error": "need magnitude or rotation"}
            _moving = True
            threading.Thread(
                target=_do_polar_move,
                args=(body.r, body.theta, body.omega, body.vel_pct),
                daemon=True,
            ).start()
            return {"ok": True, "mode": "polar"}

        _moving = True

    threading.Thread(
        target=_do_translate_rotate,
        args=(body.r, body.theta, body.omega, body.vel_pct),
        daemon=True,
    ).start()

    return {"ok": True, "mode": "translate_rotate",
            "distance_cm": body.r, "rotation_deg": body.omega}


@router.post("/wheels")
def move_wheels(body: WheelVelocities):
    send_wheels(body.wheels)
    return {"ok": True}


@router.post("/stop")
def stop():
    global _moving
    send_stop()
    _moving = False
    return {"ok": True, "message": "Motors stopped"}


@router.post("/estop")
def estop():
    global _moving
    send_estop()
    _moving = False
    return {"ok": True, "message": "Emergency stop"}


@router.get("/status")
def status():
    raw = query_motor_status()
    receiver_online = "error" not in raw
    connected = raw.get("connected", [])
    armed = raw.get("armed", [])

    motors = {}
    for nid in ALL_IDS:
        nid_str = str(nid)
        motors[nid_str] = {
            "role": MOTORS[nid]["role"],
            "armed": nid in armed,
            "error": raw.get("errors", {}).get(nid_str, 0),
            "current": raw.get("currents", {}).get(nid_str, 0.0),
            "position": raw.get("positions", {}).get(nid_str, 0.0),
            "axis_state": raw.get("axis_states", {}).get(nid_str, 0),
        }

    return {
        "connected": connected,
        "armed": armed,
        "moving": _moving or raw.get("driving", False),
        "motors": motors,
        "receiver_online": receiver_online,
        "uptime": raw.get("uptime", 0.0),
    }
