"""
Sensor data endpoints — lidar scans, camera streams, WebSocket push.
"""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from api.auth import require_auth
from api.udp import send_drive, send_stop, send_estop

router = APIRouter(prefix="/api", tags=["sensors"], dependencies=[Depends(require_auth)])

# Sensor readers are injected by server.py at startup
_camera = None  # CameraReader instance
_lidar = None   # LidarReader instance


def set_readers(camera=None, lidar=None):
    global _camera, _lidar
    _camera = camera
    _lidar = lidar


# ── Status ────────────────────────────────────────────────────────────

@router.get("/sensors/status")
def sensors_status():
    result = {}
    if _camera:
        frames = _camera.get_frames()
        result["camera"] = {
            "connected": frames["connected"],
            "fps": frames["fps"],
            "frame_count": frames["frame_count"],
            "error": frames["error"],
            "resolution": [_camera.width, _camera.height],
        }
    else:
        result["camera"] = {"connected": False, "error": "disabled"}

    if _lidar:
        scan = _lidar.get_scan()
        result["lidar"] = {
            "connected": scan["connected"],
            "scan_hz": scan["scan_hz"],
            "point_count": scan["count"],
            "port": scan["port"],
            "error": scan["error"],
        }
    else:
        result["lidar"] = {"connected": False, "error": "disabled"}

    return result


# ── Lidar ─────────────────────────────────────────────────────────────

@router.get("/sensors/lidar/scan")
def lidar_scan():
    if not _lidar:
        return {"points": [], "error": "lidar disabled"}
    return _lidar.get_scan()


# ── Camera MJPEG streams ─────────────────────────────────────────────

def _mjpeg_generator(get_jpeg_fn, fps=15):
    """Yield MJPEG frames from a callable that returns JPEG bytes."""
    interval = 1.0 / fps
    while True:
        jpeg = get_jpeg_fn()
        if jpeg:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            )
        time.sleep(interval)


@router.get("/sensors/camera/rgb", dependencies=[])  # skip auth for streams
def camera_rgb():
    if not _camera:
        return {"error": "camera disabled"}
    return StreamingResponse(
        _mjpeg_generator(_camera.get_rgb_jpeg),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/sensors/camera/depth", dependencies=[])
def camera_depth():
    if not _camera:
        return {"error": "camera disabled"}
    return StreamingResponse(
        _mjpeg_generator(_camera.get_depth_jpeg),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@router.get("/sensors/camera/points")
def camera_points(step: int = 8):
    if not _camera:
        return {"points": [], "rgb": [], "error": "camera disabled"}
    return _camera.get_depth_points(step)


# ── WebSocket — real-time sensor push ─────────────────────────────────

@router.websocket("/ws/sensors")
async def ws_sensors(websocket: WebSocket):
    await websocket.accept()

    async def _send_loop():
        while True:
            msg = {"type": "sensor_update"}

            if _lidar:
                scan = _lidar.get_scan()
                msg["lidar"] = {
                    "points": scan["points"],
                    "scan_hz": scan["scan_hz"],
                    "connected": scan["connected"],
                }
            else:
                msg["lidar"] = {"points": [], "scan_hz": 0, "connected": False}

            if _camera:
                frames = _camera.get_frames()
                msg["camera"] = {
                    "fps": frames["fps"],
                    "connected": frames["connected"],
                    "resolution": [_camera.width, _camera.height],
                }
            else:
                msg["camera"] = {"fps": 0, "connected": False, "resolution": [0, 0]}

            # Import drive state
            from api.routers.drive import is_moving
            from api.routers.paths import get_state
            path_state = get_state()
            msg["drive"] = {
                "moving": is_moving(),
                "mode": path_state.get("status", "idle"),
            }

            await websocket.send_json(msg)
            await asyncio.sleep(0.2)  # ~5 Hz

    send_task = asyncio.create_task(_send_loop())

    try:
        while True:
            data = await websocket.receive_text()
            try:
                cmd = json.loads(data)
            except json.JSONDecodeError:
                continue

            cmd_type = cmd.get("type", "")
            if cmd_type == "estop":
                send_estop()
            elif cmd_type == "stop":
                send_stop()
            elif cmd_type == "drive":
                send_drive(
                    float(cmd.get("vx", 0)),
                    float(cmd.get("vy", 0)),
                    float(cmd.get("trans_speed", 0)),
                    float(cmd.get("rot_speed", 0)),
                )
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
