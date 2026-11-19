"""
Safety filter control — enable/disable, status, config.
"""

import struct

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.udp import (
    query_motor_status, send_safety_toggle, send_safety_config,
)

router = APIRouter(prefix="/api/safety", tags=["safety"], dependencies=[Depends(require_auth)])


class SafetyConfig(BaseModel):
    min_dist_mm: int = Field(None, description="Full-stop distance in mm")
    slow_dist_mm: int = Field(None, description="Begin-decel distance in mm")
    fov_half_deg: float = Field(None, description="Movement cone half-width in degrees")


@router.post("/enable")
def enable():
    send_safety_toggle(True)
    return {"ok": True, "enabled": True}


@router.post("/disable")
def disable():
    send_safety_toggle(False)
    return {"ok": True, "enabled": False}


@router.get("/status")
def status():
    raw = query_motor_status()
    safety = raw.get("safety", {})
    return {
        "enabled": safety.get("enabled", False),
        "lidar_connected": safety.get("lidar_connected", False),
        "last_action": safety.get("last_action", "unknown"),
        "nearest_mm": safety.get("nearest_mm", -1),
        "receiver_online": "error" not in raw,
    }


@router.post("/config")
def config(body: SafetyConfig):
    min_d = body.min_dist_mm if body.min_dist_mm is not None else 250
    slow_d = body.slow_dist_mm if body.slow_dist_mm is not None else 800
    fov = body.fov_half_deg if body.fov_half_deg is not None else 60.0
    send_safety_config(min_d, slow_d, fov)
    return {"ok": True, "min_dist_mm": min_d, "slow_dist_mm": slow_d, "fov_half_deg": fov}


@router.get("/health")
def health():
    raw = query_motor_status()
    h = raw.get("health", {})
    return {
        "max_temp_c": h.get("max_temp_c", 0),
        "thermal_state": h.get("thermal_state", "unknown"),
        "jetson_temps": h.get("jetson_temps", {}),
        "vbus_min_v": h.get("vbus_min_v", 0),
        "vbus_voltages": h.get("vbus_voltages", {}),
        "battery_state": h.get("battery_state", "unknown"),
        "speed_scale": h.get("speed_scale", 1.0),
        "receiver_online": "error" not in raw,
    }
