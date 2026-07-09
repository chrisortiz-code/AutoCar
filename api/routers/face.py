"""
Face tracking control endpoints.

Wraps the face_follow_controller logic into API endpoints.
The face tracker runs as a background thread, controlled via these endpoints.
"""

import os
import socket
import struct
import threading
import time

import cv2
import numpy as np
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.udp import UDP_HOST, UDP_PORT

router = APIRouter(prefix="/api/face", tags=["face"], dependencies=[Depends(require_auth)])
stream_router = APIRouter(prefix="/api/face", tags=["face"])  # no auth for MJPEG stream

# ── State ─────────────────────────────────────────────────────────────
_lock = threading.Lock()
_state = {
    "status": "idle",       # idle | tracing | selecting | following | lost
    "tracking": False,
    "rot_speed": 0.0,
    "vx": 0.0,
    "target_depth": None,
    "current_depth": None,
    "faces": 0,
    "fps": 0.0,
}
_tracker_thread = None
_stop_event = threading.Event()
_click_queue = []      # [(x, y)]
_confirm_flag = False
_reset_flag = False
_latest_jpeg = None
_detector = None
_cap = None
_camera_reader = None  # set by set_camera_reader()


def set_camera_reader(camera):
    """Inject the shared CameraReader so face detection uses it instead of V4L2."""
    global _camera_reader
    _camera_reader = camera


# ── Configuration ─────────────────────────────────────────────────────
DEADZONE = 0.10
MAX_ROT_SPEED = 3.0
MAX_RANGE_SPEED = 5.0
DEPTH_DEADZONE_MM = 150      # ignore depth errors smaller than this
DEPTH_GAIN_MM = 1500.0       # depth error (mm) at which speed saturates
DEPTH_CLUSTER_MM = 400       # max deviation from median to include in average
CONTROL_HZ = 20
LOST_TIMEOUT = 1.0


def _clamp(value, low, high):
    return max(low, min(high, value))


def _face_depth(depth_frame, face):
    """Average depth (mm) of the body region inside a face bounding box.

    Samples the depth pixels within the bounding box, finds the median,
    then averages only pixels within DEPTH_CLUSTER_MM of that median.
    This filters out background pixels and gives a stable body depth.
    Returns 0 if no valid depth is available.
    """
    if depth_frame is None:
        return 0
    fh, fw = depth_frame.shape[:2]
    x1 = max(0, int((face.cx - face.w / 2) * fw))
    y1 = max(0, int((face.cy - face.h / 2) * fh))
    x2 = min(fw, int((face.cx + face.w / 2) * fw))
    y2 = min(fh, int((face.cy + face.h / 2) * fh))
    roi = depth_frame[y1:y2, x1:x2]
    valid = roi[roi > 0].astype(np.float32)
    if len(valid) == 0:
        return 0
    med = np.median(valid)
    cluster = valid[np.abs(valid - med) <= DEPTH_CLUSTER_MM]
    if len(cluster) == 0:
        return int(med)
    return int(np.mean(cluster))


# ── Tracker thread ────────────────────────────────────────────────────

def _tracker_loop(backend, camera_index, follow_mode):
    global _detector, _cap, _latest_jpeg, _confirm_flag, _reset_flag

    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from face_detection import create_detector

    _detector = create_detector(backend)

    # Use the shared CameraReader (RealSense) if available, otherwise fall back to V4L2
    use_realsense = _camera_reader is not None and _camera_reader.connected
    if not use_realsense:
        import platform
        if platform.system() == "Linux":
            _cap = cv2.VideoCapture(camera_index, cv2.CAP_V4L2)
        else:
            _cap = cv2.VideoCapture(camera_index)

        if not _cap.isOpened():
            with _lock:
                _state["status"] = "idle"
                _state["tracking"] = False
            return

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_dest = (UDP_HOST, UDP_PORT)
    interval = 1.0 / CONTROL_HZ
    last_control = 0.0
    last_seen = 0.0
    driving = False
    target_depth = 0  # mm
    tracking = not follow_mode
    selected_face = None
    pending_click = None   # (cx, cy) stored while confirming
    pending_depth = 0      # depth (mm) of face nearest to pending_click

    fps_count = 0
    fps_t0 = time.perf_counter()

    with _lock:
        _state["status"] = "tracing" if not follow_mode else "selecting"
        _state["tracking"] = tracking

    try:
        while not _stop_event.is_set():
            if use_realsense:
                frames = _camera_reader.get_frames()
                frame = frames.get("rgb")
                depth_frame = frames.get("depth")
                if frame is None:
                    time.sleep(0.03)
                    continue
                ok = True
            else:
                ok, frame = _cap.read()
                depth_frame = None
            if not ok:
                break

            now = time.time()

            # Handle commands
            with _lock:
                clicks = list(_click_queue)
                _click_queue.clear()
                confirm = _confirm_flag
                _confirm_flag = False
                reset = _reset_flag
                _reset_flag = False

            if follow_mode:
                if reset:
                    tracking = False
                    target_depth = 0
                    selected_face = None
                    pending_click = None
                    pending_depth = 0
                    if driving:
                        udp_sock.sendto(b"S", udp_dest)
                        driving = False
                    with _lock:
                        _state.update(status="selecting", tracking=False,
                                      target_depth=None, current_depth=None)

            # Detect faces
            faces = _detector.detect(frame)
            best = max(faces, key=lambda f: f.area) if faces else None

            # Handle clicks (follow mode — select face, wait for confirm)
            if follow_mode and clicks and faces:
                cx, cy = clicks[-1]
                pending_click = (cx, cy)
                nearest = min(faces, key=lambda f: (f.cx - cx)**2 + (f.cy - cy)**2)
                pending_depth = _face_depth(depth_frame, nearest)
                tracking = False
                if driving:
                    udp_sock.sendto(b"S", udp_dest)
                    driving = False
                with _lock:
                    _state.update(
                        status="confirming", tracking=False,
                        target_depth=pending_depth if pending_depth > 0 else None,
                        current_depth=pending_depth if pending_depth > 0 else None,
                    )

            # Handle confirm (follow mode — lock depth and start following)
            if follow_mode and confirm and pending_click is not None:
                target_depth = pending_depth if pending_depth > 0 else 0
                tracking = True
                pending_click = None
                with _lock:
                    _state.update(
                        status="following", tracking=True,
                        target_depth=target_depth if target_depth > 0 else None,
                    )

            # Control loop
            rot_speed = 0.0
            vx = 0.0
            if tracking and now - last_control >= interval:
                if best is not None:
                    last_seen = now
                    horiz_error = (best.cx - 0.5) / 0.5

                    if abs(horiz_error) >= DEADZONE:
                        rot_magnitude = (abs(horiz_error) - DEADZONE) / (1.0 - DEADZONE)
                        rot_speed = (1.0 if horiz_error > 0 else -1.0) * _clamp(rot_magnitude, 0.0, 1.0) * MAX_ROT_SPEED

                    trans_speed = 0.0
                    cur_depth = _face_depth(depth_frame, best)
                    if follow_mode and target_depth > 0 and cur_depth > 0:
                        depth_error = cur_depth - target_depth  # positive = too far
                        if abs(depth_error) >= DEPTH_DEADZONE_MM:
                            range_mag = (abs(depth_error) - DEPTH_DEADZONE_MM) / DEPTH_GAIN_MM
                            vx = (1.0 if depth_error > 0 else -1.0) * _clamp(range_mag, 0.0, 1.0)
                            trans_speed = abs(vx) * MAX_RANGE_SPEED

                    if abs(rot_speed) < 0.01 and abs(vx) < 0.01:
                        if driving:
                            udp_sock.sendto(b"S", udp_dest)
                            driving = False
                    else:
                        pkt = b"D" + struct.pack("<ffff", vx, 0.0, trans_speed, rot_speed)
                        udp_sock.sendto(pkt, udp_dest)
                        driving = True

                    status = "following" if follow_mode and target_depth > 0 else "tracing"
                else:
                    cur_depth = 0
                    if driving and (now - last_seen) >= LOST_TIMEOUT:
                        udp_sock.sendto(b"S", udp_dest)
                        driving = False
                    status = "lost" if tracking else "selecting"

                with _lock:
                    _state.update(status=status, rot_speed=rot_speed, vx=vx,
                                  faces=len(faces), tracking=tracking,
                                  target_depth=target_depth if target_depth > 0 else None,
                                  current_depth=cur_depth if cur_depth > 0 else None)
                last_control = now

            # Draw overlay and encode JPEG for stream
            display = frame.copy()
            fh, fw = display.shape[:2]

            # While confirming, find the face nearest to the pending click
            confirming_face = None
            if pending_click is not None and faces:
                pcx, pcy = pending_click
                confirming_face = min(faces, key=lambda f: (f.cx - pcx)**2 + (f.cy - pcy)**2)
                # Update depth live while confirming
                d = _face_depth(depth_frame, confirming_face)
                if d > 0:
                    pending_depth = d
                    with _lock:
                        _state["current_depth"] = d
                        _state["target_depth"] = d

            for f in faces:
                x1 = int((f.cx - f.w / 2) * fw)
                y1 = int((f.cy - f.h / 2) * fh)
                x2 = int((f.cx + f.w / 2) * fw)
                y2 = int((f.cy + f.h / 2) * fh)
                is_best = (best is not None and f is best and tracking)
                is_confirming = (confirming_face is not None and f is confirming_face)
                color = (0, 255, 0) if (is_best or is_confirming) else (100, 100, 100)
                thickness = 3 if is_confirming else 2
                cv2.rectangle(display, (x1, y1), (x2, y2), color, thickness)
                d = _face_depth(depth_frame, f)
                if d > 0:
                    label = f"{d / 1000:.2f}m"
                    cv2.putText(display, label, (x1, y1 - 6),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

            _, buf = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 70])
            with _lock:
                _latest_jpeg = buf.tobytes()

            # FPS counter
            fps_count += 1
            elapsed = time.perf_counter() - fps_t0
            if elapsed >= 1.0:
                with _lock:
                    _state["fps"] = round(fps_count / elapsed, 1)
                fps_count = 0
                fps_t0 = time.perf_counter()

            time.sleep(0.001)

    finally:
        if driving:
            udp_sock.sendto(b"S", udp_dest)
        udp_sock.close()
        if _detector:
            _detector.close()
        if _cap and not use_realsense:
            _cap.release()
        with _lock:
            _state.update(status="idle", tracking=False, rot_speed=0.0, vx=0.0)


# ── Request models ────────────────────────────────────────────────────

class ClickBody(BaseModel):
    x: float = Field(..., description="Normalized x coordinate (0-1)")
    y: float = Field(..., description="Normalized y coordinate (0-1)")


class StartBody(BaseModel):
    backend: str = "mediapipe"
    camera: int = 0
    follow: bool = False


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/start")
def start_tracking(body: StartBody = StartBody()):
    global _tracker_thread
    if _tracker_thread and _tracker_thread.is_alive():
        return {"ok": False, "error": "already running"}

    _stop_event.clear()
    _tracker_thread = threading.Thread(
        target=_tracker_loop,
        args=(body.backend, body.camera, body.follow),
        daemon=True,
    )
    _tracker_thread.start()
    return {"ok": True, "mode": "follow" if body.follow else "trace"}


@router.post("/click")
def click_face(body: ClickBody):
    with _lock:
        _click_queue.append((body.x, body.y))
    return {"ok": True}


@router.post("/confirm")
def confirm_face():
    global _confirm_flag
    with _lock:
        _confirm_flag = True
    return {"ok": True}


@router.post("/reset")
def reset_tracking():
    global _reset_flag
    with _lock:
        _reset_flag = True
    return {"ok": True}


@router.post("/stop")
def stop_tracking():
    _stop_event.set()
    if _tracker_thread:
        _tracker_thread.join(timeout=3)
    return {"ok": True}


@router.get("/status")
def face_status():
    with _lock:
        return dict(_state)


@stream_router.get("/stream")
def face_stream():
    def _generate():
        while True:
            with _lock:
                jpeg = _latest_jpeg
            if jpeg:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                )
            time.sleep(1.0 / 15)

    return StreamingResponse(
        _generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
