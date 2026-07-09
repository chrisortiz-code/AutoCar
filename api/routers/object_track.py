"""
Object tracking control endpoints.

Uses the object_tracking module (ORB, AKAZE, SIFT, template backends) to track
an arbitrary object selected via drag-to-select on the camera feed.
"""

import os
import socket
import struct
import threading
import time

import cv2
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import require_auth
from api.udp import UDP_HOST, UDP_PORT

router = APIRouter(prefix="/api/track", tags=["track"], dependencies=[Depends(require_auth)])
stream_router = APIRouter(prefix="/api/track", tags=["track"])  # no auth for MJPEG stream

# ── State ─────────────────────────────────────────────────────────────
_lock = threading.Lock()
_state = {
    "status": "idle",       # idle | streaming | tracking | lost
    "confidence": 0.0,
    "rot_speed": 0.0,
    "vx": 0.0,
    "fps": 0.0,
    "backend": "orb",
}
_tracker_thread = None
_stop_event = threading.Event()
_bbox_queue = []       # [(x1, y1, x2, y2)] normalized coords from drag
_reset_flag = False
_latest_jpeg = None
_camera_reader = None


def set_camera_reader(camera):
    """Inject the shared CameraReader so object tracking uses RealSense."""
    global _camera_reader
    _camera_reader = camera


# ── Configuration ─────────────────────────────────────────────────────
DEADZONE = 0.10
MAX_ROT_SPEED = 3.0
MAX_RANGE_SPEED = 5.0
AREA_DEADZONE = 0.08
AREA_GAIN = 0.70
CONTROL_HZ = 20
LOST_TIMEOUT = 1.0


def _clamp(value, low, high):
    return max(low, min(high, value))


# ── Tracker thread ────────────────────────────────────────────────────

def _tracker_loop(backend, follow_mode):
    global _latest_jpeg, _reset_flag

    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from object_tracking import create_matcher

    matcher = create_matcher(backend)

    use_realsense = _camera_reader is not None and _camera_reader.connected
    cap = None
    if not use_realsense:
        import platform
        if platform.system() == "Linux":
            cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
        else:
            cap = cv2.VideoCapture(0)

        if not cap.isOpened():
            with _lock:
                _state["status"] = "idle"
            return

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_dest = (UDP_HOST, UDP_PORT)
    interval = 1.0 / CONTROL_HZ
    last_control = 0.0
    last_seen = 0.0
    driving = False
    target_area = None
    has_reference = False

    fps_count = 0
    fps_t0 = time.perf_counter()

    with _lock:
        _state.update(status="streaming", backend=backend)

    try:
        while not _stop_event.is_set():
            if use_realsense:
                frames = _camera_reader.get_frames()
                frame = frames.get("rgb")
                if frame is None:
                    time.sleep(0.03)
                    continue
                ok = True
            else:
                ok, frame = cap.read()
            if not ok:
                break

            now = time.time()
            fh, fw = frame.shape[:2]

            # Handle commands
            with _lock:
                bboxes = list(_bbox_queue)
                _bbox_queue.clear()
                reset = _reset_flag
                _reset_flag = False

            if reset:
                has_reference = False
                target_area = None
                if driving:
                    udp_sock.sendto(b"S", udp_dest)
                    driving = False
                with _lock:
                    _state.update(status="streaming", confidence=0.0,
                                  rot_speed=0.0, vx=0.0)

            # Process new bounding box selection
            if bboxes:
                x1, y1, x2, y2 = bboxes[-1]
                # Convert normalized coords to pixel coords
                px1 = int(_clamp(x1, 0, 1) * fw)
                py1 = int(_clamp(y1, 0, 1) * fh)
                px2 = int(_clamp(x2, 0, 1) * fw)
                py2 = int(_clamp(y2, 0, 1) * fh)
                # Ensure proper ordering
                px1, px2 = min(px1, px2), max(px1, px2)
                py1, py2 = min(py1, py2), max(py1, py2)
                # Minimum size check
                if (px2 - px1) > 10 and (py2 - py1) > 10:
                    crop = frame[py1:py2, px1:px2]
                    matcher.set_reference(crop)
                    target_area = ((px2 - px1) * (py2 - py1)) / (fw * fh)
                    has_reference = True
                    with _lock:
                        _state["status"] = "tracking"

            # Run matching if we have a reference
            display = frame.copy()
            rot_speed = 0.0
            vx = 0.0

            if has_reference:
                match = matcher.find(frame)

                if now - last_control >= interval:
                    if match is not None:
                        last_seen = now
                        confidence = match.confidence

                        # Draw bounding box
                        bx1 = int((match.cx - match.w / 2) * fw)
                        by1 = int((match.cy - match.h / 2) * fh)
                        bx2 = int((match.cx + match.w / 2) * fw)
                        by2 = int((match.cy + match.h / 2) * fh)
                        cv2.rectangle(display, (bx1, by1), (bx2, by2), (0, 255, 0), 2)

                        # Drive control
                        if follow_mode:
                            horiz_error = (match.cx - 0.5) / 0.5
                            if abs(horiz_error) >= DEADZONE:
                                rot_magnitude = (abs(horiz_error) - DEADZONE) / (1.0 - DEADZONE)
                                rot_speed = (1.0 if horiz_error > 0 else -1.0) * _clamp(rot_magnitude, 0.0, 1.0) * MAX_ROT_SPEED

                            if target_area:
                                current_area = match.w * match.h
                                area_error = (target_area - current_area) / target_area
                                if abs(area_error) >= AREA_DEADZONE:
                                    range_mag = (abs(area_error) - AREA_DEADZONE) / max(0.01, AREA_GAIN)
                                    vx = (1.0 if area_error > 0 else -1.0) * _clamp(range_mag, 0.0, 1.0)

                            trans_speed = abs(vx) * MAX_RANGE_SPEED
                            if abs(rot_speed) < 0.01 and abs(vx) < 0.01:
                                if driving:
                                    udp_sock.sendto(b"S", udp_dest)
                                    driving = False
                            else:
                                pkt = b"D" + struct.pack("<ffff", vx, 0.0, trans_speed, rot_speed)
                                udp_sock.sendto(pkt, udp_dest)
                                driving = True

                        status = "tracking"
                        with _lock:
                            _state.update(status=status, confidence=confidence,
                                          rot_speed=rot_speed, vx=vx)
                    else:
                        if driving and (now - last_seen) >= LOST_TIMEOUT:
                            udp_sock.sendto(b"S", udp_dest)
                            driving = False
                        status = "lost" if (now - last_seen) >= LOST_TIMEOUT else "tracking"
                        with _lock:
                            _state.update(status=status, confidence=0.0,
                                          rot_speed=0.0, vx=0.0)

                    last_control = now

            # Encode JPEG for stream
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
        matcher.close()
        if cap and not use_realsense:
            cap.release()
        with _lock:
            _state.update(status="idle", confidence=0.0, rot_speed=0.0, vx=0.0, fps=0.0)


# ── Request models ────────────────────────────────────────────────────

class SetRefBody(BaseModel):
    x1: float = Field(..., description="Normalized left x (0-1)")
    y1: float = Field(..., description="Normalized top y (0-1)")
    x2: float = Field(..., description="Normalized right x (0-1)")
    y2: float = Field(..., description="Normalized bottom y (0-1)")


class TrackStartBody(BaseModel):
    backend: str = "orb"
    follow: bool = True


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/start")
def start_tracking(body: TrackStartBody = TrackStartBody()):
    global _tracker_thread
    if _tracker_thread and _tracker_thread.is_alive():
        return {"ok": False, "error": "already running"}

    _stop_event.clear()
    _tracker_thread = threading.Thread(
        target=_tracker_loop,
        args=(body.backend, body.follow),
        daemon=True,
    )
    _tracker_thread.start()
    return {"ok": True, "backend": body.backend}


@router.post("/set_ref")
def set_reference(body: SetRefBody):
    with _lock:
        _bbox_queue.append((body.x1, body.y1, body.x2, body.y2))
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
def track_status():
    with _lock:
        return dict(_state)


@stream_router.get("/stream")
def track_stream():
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
