"""
Obstacle detection (YOLO) endpoints.

Runs YOLO inference as a background thread on shared CameraReader frames,
serves annotated MJPEG stream and detection metadata.
"""

import os
import sys
import threading
import time

import cv2
import numpy as np
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import require_auth

router = APIRouter(prefix="/api/obstacles", tags=["obstacles"], dependencies=[Depends(require_auth)])
stream_router = APIRouter(prefix="/api/obstacles", tags=["obstacles"])  # no auth for MJPEG

# ── Colour palette (same as obstacle_detection/viewer.py) ────────────
_PALETTE = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
    "#469990", "#dcbeff", "#9a6324", "#800000", "#aaffc3",
    "#808000", "#ffd8b1", "#000075", "#a9a9a9", "#00ff7f",
]


def _hex_to_bgr(h):
    h = h.lstrip("#")
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def _class_colour(idx):
    return _PALETTE[idx % len(_PALETTE)]


def _class_bgr(idx):
    return _hex_to_bgr(_PALETTE[idx % len(_PALETTE)])


# ── State ────────────────────────────────────────────────────────────
_lock = threading.Lock()
_state = {
    "status": "idle",   # idle | running
    "inf_ms": 0.0,
    "fps": 0.0,
    "count": 0,
    "detections": [],   # [{cls, conf, colour}]
}
_thread = None
_stop_event = threading.Event()
_latest_jpeg = None
_camera_reader = None


def set_camera_reader(camera):
    """Inject the shared CameraReader from server.py."""
    global _camera_reader
    _camera_reader = camera


# ── Inference thread ─────────────────────────────────────────────────

def _inference_loop(model_name, conf_thresh, imgsz, world_classes):
    global _latest_jpeg

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from models import MODELS_DIR

    from ultralytics import YOLO

    def _find_model(name):
        if os.path.exists(name):
            return name
        in_models = os.path.join(MODELS_DIR, name)
        if os.path.exists(in_models):
            return in_models
        return name

    # Load model
    if world_classes:
        classes = [c.strip() for c in world_classes.split(",") if c.strip()]
        model_path = _find_model(model_name or "yolov8s-world.pt")
        model = YOLO(model_path)
        model.set_classes(classes)
    else:
        if model_name is None:
            engine = _find_model("yolo11s.engine")
            if os.path.exists(engine):
                model_path = engine
            else:
                model_path = _find_model("yolo11s.pt")
        else:
            model_path = _find_model(model_name)
        model = YOLO(model_path, task="detect")

    # Move to CUDA if not TensorRT
    is_engine = (model_path or "").endswith(".engine")
    if not is_engine:
        import torch
        if torch.cuda.is_available():
            model.to("cuda")

    with _lock:
        _state["status"] = "running"

    fps_count = 0
    fps_t0 = time.perf_counter()

    try:
        while not _stop_event.is_set():
            if _camera_reader is None or not _camera_reader.connected:
                time.sleep(0.1)
                continue

            frames = _camera_reader.get_frames()
            frame = frames.get("rgb")
            if frame is None:
                time.sleep(0.03)
                continue

            t0 = time.perf_counter()
            results = model(frame, verbose=False, imgsz=imgsz, conf=conf_thresh)
            inf_ms = (time.perf_counter() - t0) * 1000

            annotated = frame.copy()
            dets = []
            for r in results:
                for box in r.boxes:
                    cls_id = int(box.cls)
                    cls_name = model.names[cls_id]
                    c = float(box.conf)
                    colour_bgr = _class_bgr(cls_id)
                    colour_hex = _class_colour(cls_id)

                    x1, y1, x2, y2 = box.xyxy[0].int().tolist()
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), colour_bgr, 2)
                    label = f"{cls_name} {c:.0%}"
                    (tw, th), _ = cv2.getTextSize(
                        label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                    cv2.rectangle(annotated, (x1, max(y1 - th - 6, 0)),
                                  (x1 + tw + 4, y1), colour_bgr, -1)
                    cv2.putText(annotated, label,
                                (x1 + 2, max(y1 - 4, th + 2)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                                (255, 255, 255), 1)

                    dets.append({"cls": cls_name, "conf": c, "colour": colour_hex})

            dets.sort(key=lambda d: d["conf"], reverse=True)

            _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
            with _lock:
                _latest_jpeg = buf.tobytes()
                _state["inf_ms"] = inf_ms
                _state["count"] = len(dets)
                _state["detections"] = dets

            fps_count += 1
            elapsed = time.perf_counter() - fps_t0
            if elapsed >= 1.0:
                with _lock:
                    _state["fps"] = round(fps_count / elapsed, 1)
                fps_count = 0
                fps_t0 = time.perf_counter()

            time.sleep(0.001)

    finally:
        with _lock:
            _state.update(status="idle", inf_ms=0.0, fps=0.0, count=0, detections=[])


# ── Request models ───────────────────────────────────────────────────

class StartBody(BaseModel):
    model: str | None = None
    conf: float = 0.25
    imgsz: int = 640
    world: str | None = None


# ── Endpoints ────────────────────────────────────────────────────────

@router.post("/start")
def start_obstacles(body: StartBody = StartBody()):
    global _thread
    if _thread and _thread.is_alive():
        return {"ok": False, "error": "already running"}

    _stop_event.clear()
    _thread = threading.Thread(
        target=_inference_loop,
        args=(body.model, body.conf, body.imgsz, body.world),
        daemon=True,
    )
    _thread.start()
    return {"ok": True}


@router.post("/stop")
def stop_obstacles():
    _stop_event.set()
    if _thread:
        _thread.join(timeout=5)
    return {"ok": True}


@router.get("/status")
def obstacles_status():
    with _lock:
        return dict(_state)


@stream_router.get("/stream")
def obstacles_stream():
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
