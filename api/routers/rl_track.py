"""
RL policy tracking endpoints.

Uses CSRT for target detection, paints the bounding box green, and feeds the
modified frame to a trained recurrent RL policy (CNN+LSTM) that outputs motor
commands. The policy was trained in IsaacLab simulation to follow green targets.

Pipeline:
    CSRT (every frame) → green overlay → RL policy (4 Hz) → UDP motor commands
"""

import math
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

router = APIRouter(prefix="/api/rl", tags=["rl"], dependencies=[Depends(require_auth)])
stream_router = APIRouter(prefix="/api/rl", tags=["rl"])

# ── State ─────────────────────────────────────────────────────────────
_lock = threading.Lock()
_state = {
    "status": "idle",       # idle | loading | streaming | tracking | lost
    "status_msg": "",
    "confidence": 0.0,
    "actions": [0.0, 0.0, 0.0],
    "lstm_step": 0,
    "fps": 0.0,
    "desired_dist": 1.0,
    "target_depth": 0,      # mm
    "cur_depth": 0,          # mm
    "csrt_ms": 0.0,
    "preprocess_ms": 0.0,
    "policy_ms": 0.0,
    "total_ms": 0.0,
    "motor_cmd": [0.0, 0.0, 0.0, 0.0],
    "model_variant": "",
    "model_name": "",
    "action_dim": 3,
    "safety_active": False,
}
_tracker_thread = None
_stop_event = threading.Event()
_bbox_queue = []
_reset_flag = False
_latest_jpeg = None
_latest_depth_jpeg = None
_camera_reader = None
_lidar_reader = None
_cached_runner = None
_cached_runner_path = None


def set_camera_reader(camera):
    """Inject the shared CameraReader."""
    global _camera_reader
    _camera_reader = camera


def set_lidar_reader(lidar):
    """Inject the shared LidarProxy/LidarReader."""
    global _lidar_reader
    _lidar_reader = lidar


# ── Configuration ─────────────────────────────────────────────────────

# Policy runs at 4 Hz (matching sim decimation: dt=0.01667 × 15 steps)
POLICY_HZ = 4

# Action scaling — policy outputs [-1,1] are normalized, scale up for real robot
FORWARD_SCALE = 5.0
STRAFE_SCALE = 5.0
YAW_SCALE = 5.0

# Motor limits in turns/s (ODrive velocity units)
MAX_ROT_SPEED = 10.0
MAX_RANGE_SPEED = 10.0

# Target loss timeouts
COAST_TIMEOUT = 0.5     # seconds: coast on last command
STOP_TIMEOUT = 2.0      # seconds: stop and reset LSTM

# Default model checkpoint — PolicyRunner handles traced-only files
_CKPT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "rl_policy", "checkpoints")
_DEFAULT_MODEL = "depthonly_curriculum.pt"
DEFAULT_CHECKPOINT = ""
if os.path.isdir(_CKPT_DIR):
    _preferred = os.path.join(_CKPT_DIR, _DEFAULT_MODEL)
    _preferred_traced = _preferred.replace(".pt", "_traced_fp16.pt")
    if os.path.isfile(_preferred) or os.path.isfile(_preferred_traced):
        DEFAULT_CHECKPOINT = _preferred
    else:
        _candidates = sorted(
            f for f in os.listdir(_CKPT_DIR)
            if f.endswith(".pt") and "_traced" not in f
        )
        if _candidates:
            DEFAULT_CHECKPOINT = os.path.join(_CKPT_DIR, _candidates[0])


def _clamp(value, low, high):
    return max(low, min(high, value))


def _draw_text(img, text, pos, color):
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


def _scale_actions(raw_actions):
    """Convert policy outputs to UDP D-packet values.

    The policy outputs body-frame [forward, strafe, yaw], each roughly [-1, 1].
    The D-packet expects (vx, vy, trans_speed, rot_speed) where vx/vy is a
    direction unit vector and trans_speed is the magnitude.

    This is equivalent to converting (fwd, strafe) → (theta, speed):
        theta = atan2(strafe, fwd)   → encoded as unit vector (vx=cos, vy=sin)
        speed = norm(fwd, strafe)    → scaled to motor turns/s

    Args:
        raw_actions: [forward, strafe, yaw] roughly [-1, 1].

    Returns:
        (vx, vy, trans_speed, rot_speed) for the D packet.
    """
    if len(raw_actions) == 2:
        fwd = raw_actions[0] * FORWARD_SCALE
        strafe = 0.0
        yaw = raw_actions[1] * YAW_SCALE
    else:
        fwd = raw_actions[0] * FORWARD_SCALE
        strafe = raw_actions[1] * STRAFE_SCALE
        yaw = raw_actions[2] * YAW_SCALE

    # (fwd, strafe) → direction + magnitude, matching drive(vx, vy, speed, rot)
    magnitude = math.sqrt(fwd ** 2 + strafe ** 2)
    if magnitude > 0.01:
        # Unit vector = direction theta as (cos, sin)
        vx = fwd / magnitude
        vy = strafe / magnitude
        trans_speed = min(magnitude, 1.0) * MAX_RANGE_SPEED
    else:
        vx = 0.0
        vy = 0.0
        trans_speed = 0.0

    rot_speed = _clamp(yaw * MAX_ROT_SPEED, -MAX_ROT_SPEED, MAX_ROT_SPEED)
    return vx, vy, trans_speed, rot_speed


# ── Tracker thread ────────────────────────────────────────────────────

def _load_model_async(checkpoint_path, result_holder, stop_event):
    """Load PolicyRunner on a background thread, storing it in result_holder[0]."""
    global _cached_runner, _cached_runner_path
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from rl_policy.inference import PolicyRunner

    if _cached_runner is not None and _cached_runner_path == checkpoint_path:
        _cached_runner.reset()
        result_holder[0] = _cached_runner
        return

    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        device = "cpu"

    runner = PolicyRunner(checkpoint_path, device=device)
    if not stop_event.is_set():
        _cached_runner = runner
        _cached_runner_path = checkpoint_path
        result_holder[0] = runner


def _rl_tracker_loop(desired_dist_m, checkpoint_path, backend="csrt"):
    global _latest_jpeg, _latest_depth_jpeg, _reset_flag

    with _lock:
        _state["status"] = "loading"
        _state["status_msg"] = "Connecting camera..."

    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from object_tracking.base import create_matcher

    # Start camera immediately
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
                _state["status_msg"] = "Camera failed to open"
            return

    # Load model in parallel while camera streams
    runner_holder = [None]
    model_thread = threading.Thread(
        target=_load_model_async,
        args=(checkpoint_path, runner_holder, _stop_event),
        daemon=True,
    )
    model_thread.start()

    with _lock:
        _state["status_msg"] = "Loading RL model..."

    tracker = create_matcher(backend, fallback=True)

    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_dest = (UDP_HOST, UDP_PORT)
    policy_interval = 1.0 / POLICY_HZ
    last_policy_tick = 0.0
    last_seen = 0.0
    driving = False
    has_reference = False
    frozen_until = 0.0
    frozen_jpeg = None
    runner = None

    fps_count = 0
    fps_t0 = time.perf_counter()

    with _lock:
        _state.update(status="streaming", status_msg="Loading RL model...",
                      desired_dist=desired_dist_m)

    try:
        while not _stop_event.is_set():
            # ── Check if model finished loading ──
            if runner is None and runner_holder[0] is not None:
                runner = runner_holder[0]
                ckpt_name = os.path.splitext(os.path.basename(checkpoint_path))[0]
                with _lock:
                    _state["model_variant"] = (
                        "depth" if runner.model.cnn_channels == 1 else "rgbd"
                    ) + f"_{runner.model.action_dim}a"
                    _state["model_name"] = ckpt_name
                    _state["action_dim"] = runner.action_dim
                    _state["status_msg"] = ""

            # ── Get camera frame ──
            depth_frame = None
            if use_realsense:
                frames = _camera_reader.get_frames()
                frame = frames.get("rgb")
                depth_frame = frames.get("depth")
                if frame is None:
                    time.sleep(0.03)
                    continue
            else:
                ok, frame = cap.read()
                if not ok:
                    break

            now = time.time()
            fh, fw = frame.shape[:2]

            # ── Handle commands ──
            with _lock:
                bboxes = list(_bbox_queue)
                _bbox_queue.clear()
                reset = _reset_flag
                _reset_flag = False

            if reset:
                has_reference = False
                frozen_jpeg = None
                frozen_until = 0.0
                if runner is not None:
                    runner.reset()
                if driving:
                    udp_sock.sendto(b"S", udp_dest)
                    driving = False
                with _lock:
                    _state.update(status="streaming", confidence=0.0,
                                  actions=[0.0, 0.0, 0.0], lstm_step=0,
                                  target_depth=0, cur_depth=0)

            # ── Process new bounding box ──
            if bboxes and runner is not None:
                x1, y1, x2, y2 = bboxes[-1]
                px1 = int(_clamp(x1, 0, 1) * fw)
                py1 = int(_clamp(y1, 0, 1) * fh)
                px2 = int(_clamp(x2, 0, 1) * fw)
                py2 = int(_clamp(y2, 0, 1) * fh)
                px1, px2 = min(px1, px2), max(px1, px2)
                py1, py2 = min(py1, py2), max(py1, py2)

                if (px2 - px1) > 10 and (py2 - py1) > 10:
                    tracker.set_reference_roi(frame, px1, py1, px2, py2)
                    runner.reset()
                    has_reference = True
                    # Freeze stream briefly
                    snap = frame.copy()
                    cv2.rectangle(snap, (px1, py1), (px2, py2), (0, 255, 255), 2)
                    _draw_text(snap, "RL target set", (px1, max(py1 - 8, 14)), (0, 255, 255))
                    _, fbuf = cv2.imencode(".jpg", snap, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    frozen_jpeg = fbuf.tobytes()
                    frozen_until = time.time() + 1.0
                    with _lock:
                        _state["status"] = "tracking"
                        _latest_jpeg = frozen_jpeg

            # While frozen, keep outputting the frozen frame
            if frozen_jpeg and time.time() < frozen_until:
                with _lock:
                    _latest_jpeg = frozen_jpeg
                time.sleep(0.03)
                continue
            frozen_jpeg = None

            # ── Run CSRT on every frame ──
            display = frame.copy()
            bbox = None

            if has_reference and runner is not None:
                t_csrt = time.perf_counter()
                bbox = tracker.find(frame)
                csrt_ms = (time.perf_counter() - t_csrt) * 1000.0
                if bbox is not None:
                    last_seen = now

                # ── Run policy at POLICY_HZ ──
                if now - last_policy_tick >= policy_interval:
                    if bbox is not None:
                        confidence = bbox.confidence

                        # Get lidar scan
                        scan_points = []
                        lidar_ok = False
                        if _lidar_reader is not None:
                            scan_data = _lidar_reader.get_scan()
                            scan_points = scan_data.get("points", [])
                            lidar_ok = len(scan_points) > 0

                        # Provide depth (zeros if no RealSense)
                        if depth_frame is None:
                            depth_frame = np.zeros((fh, fw), dtype=np.uint16)

                        t_pre = time.perf_counter()
                        # Run policy
                        raw_actions = runner.step(
                            frame, depth_frame, bbox, scan_points, desired_dist_m,
                        )
                        policy_ms = (time.perf_counter() - t_pre) * 1000.0

                        # Block motor output if lidar-equipped model has no lidar data
                        if runner.has_lidar and not lidar_ok:
                            raw_actions = [0.0] * len(raw_actions)
                            with _lock:
                                _state["safety_active"] = True

                        # Scale actions to motor commands
                        vx, vy, trans_speed, rot_speed = _scale_actions(raw_actions)
                        motor_cmd = [vx, vy, trans_speed, rot_speed]

                        if abs(trans_speed) < 0.01 and abs(rot_speed) < 0.01:
                            if driving:
                                udp_sock.sendto(b"S", udp_dest)
                                driving = False
                        else:
                            pkt = b"D" + struct.pack("<ffff", vx, vy, trans_speed, rot_speed)
                            udp_sock.sendto(pkt, udp_dest)
                            driving = True

                        # Sample current depth at target
                        cur_depth = 0
                        if depth_frame is not None:
                            cpx = int(bbox.cx * fw)
                            cpy = int(bbox.cy * fh)
                            r = max(1, int(min(bbox.w, bbox.h) * min(fw, fh) * 0.1))
                            roi = depth_frame[
                                max(0, cpy - r):min(fh, cpy + r),
                                max(0, cpx - r):min(fw, cpx + r),
                            ]
                            valid = roi[roi > 0]
                            cur_depth = int(np.median(valid)) if len(valid) > 0 else 0

                        total_ms = csrt_ms + policy_ms
                        with _lock:
                            _state.update(
                                status="tracking",
                                confidence=confidence,
                                actions=raw_actions,
                                lstm_step=runner.step_count,
                                cur_depth=cur_depth,
                                csrt_ms=round(csrt_ms, 1),
                                policy_ms=round(policy_ms, 1),
                                total_ms=round(total_ms, 1),
                                motor_cmd=motor_cmd,
                                safety_active=False,
                            )
                    else:
                        # Target lost — graduated fallback
                        time_lost = now - last_seen
                        if time_lost >= COAST_TIMEOUT:
                            if driving:
                                udp_sock.sendto(b"S", udp_dest)
                                driving = False
                        if time_lost >= STOP_TIMEOUT:
                            runner.reset()

                        status = "lost" if (now - last_seen) >= COAST_TIMEOUT else "tracking"
                        with _lock:
                            _state.update(
                                status=status,
                                confidence=0.0,
                                actions=[0.0, 0.0, 0.0],
                                lstm_step=runner.step_count,
                            )

                    last_policy_tick = now

            # ── Loading overlay ──
            if runner is None:
                _draw_text(display, "Loading RL model...", (8, 20), (0, 255, 255))

            # ── Build display frame ──
            if bbox is not None:
                # Paint green overlay on display for debug
                bx1 = max(0, int((bbox.cx - bbox.w / 2) * fw))
                by1 = max(0, int((bbox.cy - bbox.h / 2) * fh))
                bx2 = min(fw, int((bbox.cx + bbox.w / 2) * fw))
                by2 = min(fh, int((bbox.cy + bbox.h / 2) * fh))

                # Semi-transparent green overlay (so user can still see what's underneath)
                overlay = display.copy()
                overlay[by1:by2, bx1:bx2] = (0, 255, 0)
                cv2.addWeighted(overlay, 0.4, display, 0.6, 0, display)

                # Bbox outline
                cv2.rectangle(display, (bx1, by1), (bx2, by2), (255, 255, 0), 2)

                # Labels
                with _lock:
                    acts = _state["actions"]
                    step = _state["lstm_step"]
                    cdepth = _state["cur_depth"]
                _draw_text(display, f"RL step {step}", (8, 20), (0, 255, 255))
                _draw_text(display, f"fwd={acts[0]:+.2f} str={acts[1]:+.2f} yaw={acts[2]:+.2f}",
                           (8, 40), (0, 255, 255))
                _draw_text(display, f"conf={bbox.confidence:.0%}", (bx1, max(by1 - 8, 14)), (0, 255, 0))
                if cdepth > 0:
                    _draw_text(display, f"{cdepth / 1000:.2f}m", (bx1, by2 + 16), (0, 255, 0))
            elif has_reference:
                _draw_text(display, "TARGET LOST", (8, 20), (0, 0, 255))

            # ── Annotated depth feed ──
            if depth_frame is not None:
                depth_norm = np.clip(depth_frame.astype(np.float32) / 10000.0, 0, 1)
                depth_color = cv2.applyColorMap(
                    (depth_norm * 255).astype(np.uint8), cv2.COLORMAP_TURBO,
                )
                if bbox is not None:
                    dbx1 = max(0, int((bbox.cx - bbox.w / 2) * fw))
                    dby1 = max(0, int((bbox.cy - bbox.h / 2) * fh))
                    dbx2 = min(fw, int((bbox.cx + bbox.w / 2) * fw))
                    dby2 = min(fh, int((bbox.cy + bbox.h / 2) * fh))
                    overlay_d = depth_color.copy()
                    overlay_d[dby1:dby2, dbx1:dbx2] = (0, 255, 0)
                    cv2.addWeighted(overlay_d, 0.3, depth_color, 0.7, 0, depth_color)
                    cv2.rectangle(depth_color, (dbx1, dby1), (dbx2, dby2), (0, 255, 0), 2)
                    with _lock:
                        cdepth = _state["cur_depth"]
                    if cdepth > 0:
                        _draw_text(depth_color, f"{cdepth / 1000:.2f}m",
                                   (dbx1, dby2 + 16), (0, 255, 0))
                _, dbuf = cv2.imencode(".jpg", depth_color, [cv2.IMWRITE_JPEG_QUALITY, 70])
                with _lock:
                    _latest_depth_jpeg = dbuf.tobytes()

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
        tracker.close()
        if cap and not use_realsense:
            cap.release()
        model_thread.join(timeout=10)
        with _lock:
            _state.update(status="idle", confidence=0.0, actions=[0.0, 0.0, 0.0],
                          lstm_step=0, fps=0.0)


# ── Request models ────────────────────────────────────────────────────

class SetRefBody(BaseModel):
    x1: float = Field(..., description="Normalized left x (0-1)")
    y1: float = Field(..., description="Normalized top y (0-1)")
    x2: float = Field(..., description="Normalized right x (0-1)")
    y2: float = Field(..., description="Normalized bottom y (0-1)")


class RLStartBody(BaseModel):
    desired_dist: float = Field(1.0, description="Target follow distance in meters")
    checkpoint: str = Field("", description="Model checkpoint path (empty = default)")
    backend: str = Field("csrt", description="Tracker backend: csrt, orb, akaze, sift, lightglue")


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/start")
def start_rl(body: RLStartBody = RLStartBody()):
    global _tracker_thread
    if _tracker_thread and _tracker_thread.is_alive():
        return {"ok": False, "error": "already running"}

    checkpoint = body.checkpoint if body.checkpoint else DEFAULT_CHECKPOINT
    traced = checkpoint.replace(".pt", "_traced_fp16.pt")
    if not os.path.exists(checkpoint) and not os.path.exists(traced):
        return {"ok": False, "error": f"checkpoint not found: {checkpoint}"}

    _stop_event.clear()
    _tracker_thread = threading.Thread(
        target=_rl_tracker_loop,
        args=(body.desired_dist, checkpoint, body.backend),
        daemon=True,
    )
    _tracker_thread.start()
    return {"ok": True, "desired_dist": body.desired_dist, "checkpoint": checkpoint, "backend": body.backend}


@router.post("/set_ref")
def set_reference(body: SetRefBody):
    with _lock:
        _bbox_queue.append((body.x1, body.y1, body.x2, body.y2))
    return {"ok": True}


@router.post("/reset")
def reset_rl():
    global _reset_flag
    with _lock:
        _reset_flag = True
    return {"ok": True}


@router.post("/stop")
def stop_rl():
    _stop_event.set()
    if _tracker_thread:
        _tracker_thread.join(timeout=5)
    return {"ok": True}


@router.get("/checkpoints")
def list_checkpoints():
    if not os.path.isdir(_CKPT_DIR):
        return {"checkpoints": []}
    raw = {f for f in os.listdir(_CKPT_DIR) if f.endswith(".pt") and "_traced" not in f}
    traced_only = {
        f.replace("_traced_fp16.pt", ".pt")
        for f in os.listdir(_CKPT_DIR)
        if f.endswith("_traced_fp16.pt") and f.replace("_traced_fp16.pt", ".pt") not in raw
    }
    files = sorted(raw | traced_only)
    return {"checkpoints": files}


@router.get("/status")
def rl_status():
    with _lock:
        return dict(_state)


@stream_router.get("/stream")
def rl_stream():
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


@stream_router.get("/depth_stream")
def rl_depth_stream():
    def _generate():
        while True:
            with _lock:
                jpeg = _latest_depth_jpeg
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
