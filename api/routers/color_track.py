"""
Color tracking control endpoints.

Click-to-pick color tracker: user clicks on a color in the camera feed,
the tracker finds the largest blob of that color using HSV inRange.
Optionally follows with depth-based range control from RealSense.
"""

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

router = APIRouter(prefix="/api/color", tags=["color"], dependencies=[Depends(require_auth)])
stream_router = APIRouter(prefix="/api/color", tags=["color"])

# ── State ─────────────────────────────────────────────────────────────
_lock = threading.Lock()
_state = {
    "status": "idle",       # idle | loading | streaming | picking | tracking | lost
    "status_msg": "",
    "picked_color": None,   # [R, G, B] for display
    "confidence": 0.0,
    "rot_speed": 0.0,
    "vx": 0.0,
    "target_depth": None,
    "current_depth": None,
    "fps": 0.0,
}
_tracker_thread = None
_stop_event = threading.Event()
_click_queue = []
_confirm_flag = False
_reset_flag = False
_latest_jpeg = None
_camera_reader = None


def set_camera_reader(camera):
    """Inject the shared CameraReader."""
    global _camera_reader
    _camera_reader = camera


# ── Configuration ─────────────────────────────────────────────────────
HSV_TOL_H = 30
HSV_TOL_S = 80
HSV_TOL_V = 80
MIN_BLOB = 500
SAMPLE_SIZE = 5

DEADZONE = 0.10
MAX_ROT_SPEED = 3.0
MAX_RANGE_SPEED = 5.0
DEPTH_DEADZONE_MM = 150
DEPTH_GAIN_MM = 1500.0
DEPTH_CLUSTER_MM = 400
CONTROL_HZ = 20
LOST_TIMEOUT = 1.0

_MORPH_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))


def _clamp(value, low, high):
    return max(low, min(high, value))


def _sample_color(frame_bgr, x, y):
    """Average a small region around (x, y) and return HSV + RGB."""
    h, w = frame_bgr.shape[:2]
    half = SAMPLE_SIZE // 2
    x0, x1 = max(0, x - half), min(w, x + half + 1)
    y0, y1 = max(0, y - half), min(h, y + half + 1)
    region = frame_bgr[y0:y1, x0:x1]
    avg_bgr = region.mean(axis=(0, 1)).astype(np.uint8)
    avg_hsv = cv2.cvtColor(avg_bgr.reshape(1, 1, 3), cv2.COLOR_BGR2HSV)[0, 0]
    avg_rgb = [int(avg_bgr[2]), int(avg_bgr[1]), int(avg_bgr[0])]
    return tuple(int(v) for v in avg_hsv), avg_rgb


def _find_blob(frame_bgr, hsv_center):
    """Find the largest blob matching hsv_center. Returns (cx, cy, area, contour, mask) or None."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv_center
    lo_s, hi_s = max(0, s - HSV_TOL_S), min(255, s + HSV_TOL_S)
    lo_v, hi_v = max(0, v - HSV_TOL_V), min(255, v + HSV_TOL_V)

    if h - HSV_TOL_H < 0:
        m1 = cv2.inRange(hsv, np.array([0, lo_s, lo_v]),
                         np.array([h + HSV_TOL_H, hi_s, hi_v]))
        m2 = cv2.inRange(hsv, np.array([180 + h - HSV_TOL_H, lo_s, lo_v]),
                         np.array([179, hi_s, hi_v]))
        mask = cv2.bitwise_or(m1, m2)
    elif h + HSV_TOL_H > 179:
        m1 = cv2.inRange(hsv, np.array([h - HSV_TOL_H, lo_s, lo_v]),
                         np.array([179, hi_s, hi_v]))
        m2 = cv2.inRange(hsv, np.array([0, lo_s, lo_v]),
                         np.array([h + HSV_TOL_H - 180, hi_s, hi_v]))
        mask = cv2.bitwise_or(m1, m2)
    else:
        mask = cv2.inRange(hsv, np.array([h - HSV_TOL_H, lo_s, lo_v]),
                           np.array([h + HSV_TOL_H, hi_s, hi_v]))

    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _MORPH_KERNEL)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _MORPH_KERNEL)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    biggest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(biggest)
    if area < MIN_BLOB:
        return None

    M = cv2.moments(biggest)
    if M["m00"] == 0:
        return None

    cx = int(M["m10"] / M["m00"])
    cy = int(M["m01"] / M["m00"])
    return cx, cy, area, biggest


def _sample_depth(depth_frame, px, py, radius=30):
    """Sample clustered depth around a pixel coordinate."""
    if depth_frame is None:
        return 0
    fh, fw = depth_frame.shape[:2]
    x0, x1 = max(0, px - radius), min(fw, px + radius)
    y0, y1 = max(0, py - radius), min(fh, py + radius)
    roi = depth_frame[y0:y1, x0:x1]
    valid = roi[roi > 0].astype(np.float32)
    if len(valid) == 0:
        return 0
    med = np.median(valid)
    cluster = valid[np.abs(valid - med) <= DEPTH_CLUSTER_MM]
    return int(np.mean(cluster)) if len(cluster) > 0 else int(med)


def _draw_text(img, text, pos, color):
    """Draw outlined text for visibility on any background."""
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


# ── Tracker thread ────────────────────────────────────────────────────

def _tracker_loop(follow_mode):
    global _latest_jpeg, _confirm_flag, _reset_flag

    with _lock:
        _state["status"] = "loading"
        _state["status_msg"] = "Connecting camera..."

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
                _state.update(status="idle", status_msg="Camera failed to open")
            return

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_dest = (UDP_HOST, UDP_PORT)
    interval = 1.0 / CONTROL_HZ
    last_control = 0.0
    last_seen = 0.0
    driving = False

    picked_hsv = None
    picked_rgb = None
    target_depth = 0
    tracking = False

    fps_count = 0
    fps_t0 = time.perf_counter()

    with _lock:
        _state.update(status="streaming", status_msg="")

    try:
        while not _stop_event.is_set():
            if use_realsense:
                frames = _camera_reader.get_frames()
                frame = frames.get("rgb")
                depth_frame = frames.get("depth")
                if frame is None:
                    time.sleep(0.03)
                    continue
            else:
                ok, frame = cap.read()
                depth_frame = None
                if not ok:
                    break

            now = time.time()
            fh, fw = frame.shape[:2]

            # Handle commands
            with _lock:
                clicks = list(_click_queue)
                _click_queue.clear()
                confirm = _confirm_flag
                _confirm_flag = False
                reset = _reset_flag
                _reset_flag = False

            if reset:
                picked_hsv = None
                picked_rgb = None
                target_depth = 0
                tracking = False
                if driving:
                    udp_sock.sendto(b"S", udp_dest)
                    driving = False
                with _lock:
                    _state.update(status="streaming", picked_color=None,
                                  confidence=0.0, rot_speed=0.0, vx=0.0,
                                  target_depth=None, current_depth=None)

            # Handle click — pick color
            if clicks:
                cx_click, cy_click = clicks[-1]
                px = int(_clamp(cx_click, 0, 1) * fw)
                py = int(_clamp(cy_click, 0, 1) * fh)
                picked_hsv, picked_rgb = _sample_color(frame, px, py)
                # capture depth at click location
                click_depth = _sample_depth(depth_frame, px, py)
                tracking = False
                if driving:
                    udp_sock.sendto(b"S", udp_dest)
                    driving = False
                with _lock:
                    _state.update(
                        status="picking", picked_color=picked_rgb,
                        target_depth=click_depth if click_depth > 0 else None,
                        current_depth=click_depth if click_depth > 0 else None,
                    )
                target_depth = click_depth

            # Handle confirm — lock color + depth, start tracking
            if confirm and picked_hsv is not None:
                tracking = True
                with _lock:
                    _state.update(status="tracking")

            # Run detection + control
            display = frame.copy()
            rot_speed = 0.0
            vx = 0.0

            if picked_hsv is not None:
                result = _find_blob(frame, picked_hsv)

                # Draw color swatch
                if picked_rgb:
                    bgr = (picked_rgb[2], picked_rgb[1], picked_rgb[0])
                    cv2.rectangle(display, (8, 8), (40, 40), bgr, -1)
                    cv2.rectangle(display, (8, 8), (40, 40), (255, 255, 255), 1)

                if result is not None:
                    bx, by, area, contour = result
                    cv2.drawContours(display, [contour], -1, (0, 255, 0), 2)
                    cv2.circle(display, (bx, by), 5, (0, 0, 255), -1)
                    confidence = min(area / (fw * fh * 0.5), 1.0)

                    if tracking and now - last_control >= interval:
                        last_seen = now

                        # rotation control
                        horiz_error = (bx / fw - 0.5) / 0.5
                        if abs(horiz_error) >= DEADZONE:
                            rot_mag = (abs(horiz_error) - DEADZONE) / (1.0 - DEADZONE)
                            rot_speed = (1.0 if horiz_error > 0 else -1.0) * _clamp(rot_mag, 0.0, 1.0) * MAX_ROT_SPEED

                        # range control — depth preferred, area fallback
                        cur_depth = _sample_depth(depth_frame, bx, by)
                        if follow_mode and target_depth > 0 and cur_depth > 0:
                            depth_error = cur_depth - target_depth
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

                        with _lock:
                            _state.update(
                                status="tracking", confidence=confidence,
                                rot_speed=rot_speed, vx=vx,
                                current_depth=cur_depth if cur_depth > 0 else None,
                            )
                        last_control = now

                    # label even when not tracking (picking state)
                    d = _sample_depth(depth_frame, bx, by)
                    if d > 0:
                        _draw_text(display, f"{d/1000:.2f}m",
                                   (bx - 20, by - 15), (0, 255, 0))

                elif tracking:
                    if now - last_control >= interval:
                        if driving and (now - last_seen) >= LOST_TIMEOUT:
                            udp_sock.sendto(b"S", udp_dest)
                            driving = False
                        status = "lost" if (now - last_seen) >= LOST_TIMEOUT else "tracking"
                        with _lock:
                            _state.update(status=status, confidence=0.0,
                                          rot_speed=0.0, vx=0.0)
                        last_control = now

            # Status text (only show critical overlay — UI handles idle/picking prompts)
            with _lock:
                cur_status = _state["status"]
            if cur_status == "lost":
                _draw_text(display, "TARGET LOST", (8, fh - 12), (0, 0, 255))

            # Encode JPEG
            _, buf = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 70])
            with _lock:
                _latest_jpeg = buf.tobytes()

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
        if cap and not use_realsense:
            cap.release()
        with _lock:
            _state.update(status="idle", confidence=0.0, rot_speed=0.0, vx=0.0,
                          fps=0.0, picked_color=None, target_depth=None, current_depth=None)


# ── Request models ────────────────────────────────────────────────────

class ColorClickBody(BaseModel):
    x: float = Field(..., description="Normalized x coordinate (0-1)")
    y: float = Field(..., description="Normalized y coordinate (0-1)")


class ColorStartBody(BaseModel):
    follow: bool = True


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/start")
def start_color(body: ColorStartBody = ColorStartBody()):
    global _tracker_thread
    if _tracker_thread and _tracker_thread.is_alive():
        return {"ok": False, "error": "already running"}
    _stop_event.clear()
    _tracker_thread = threading.Thread(target=_tracker_loop, args=(body.follow,), daemon=True)
    _tracker_thread.start()
    return {"ok": True}


@router.post("/click")
def click_color(body: ColorClickBody):
    with _lock:
        _click_queue.append((body.x, body.y))
    return {"ok": True}


@router.post("/confirm")
def confirm_color():
    global _confirm_flag
    with _lock:
        _confirm_flag = True
    return {"ok": True}


@router.post("/reset")
def reset_color():
    global _reset_flag
    with _lock:
        _reset_flag = True
    return {"ok": True}


@router.post("/stop")
def stop_color():
    _stop_event.set()
    if _tracker_thread:
        _tracker_thread.join(timeout=3)
    return {"ok": True}


@router.get("/status")
def color_status():
    with _lock:
        return dict(_state)


@stream_router.get("/stream")
def color_stream():
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
