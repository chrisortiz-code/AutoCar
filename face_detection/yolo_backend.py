"""YOLO face detection backend (ultralytics).

Supports .pt, .onnx, and .engine (TensorRT) model files.
If a .engine file exists alongside the .pt, it is used automatically.

Get a YOLO face model:
    yolo export model=yolov8n-face.pt format=engine half=True imgsz=640
"""

import os
import urllib.request

from .base import FaceDetector, Detection

DEFAULT_MODEL = "yolov8n-face.pt"
MODEL_URL = "https://github.com/akanametov/yolov8-face/releases/download/v0.0.0/yolov8n-face.pt"
MODEL_DIR = os.path.dirname(os.path.abspath(__file__))


def _ensure_model(model_path):
    if os.path.exists(model_path):
        return model_path
    dest = os.path.join(MODEL_DIR, os.path.basename(model_path))
    if os.path.exists(dest):
        return dest
    print(f"[yolo] Downloading {os.path.basename(model_path)} ...")
    urllib.request.urlretrieve(MODEL_URL, dest)
    print(f"[yolo] Saved to {dest}")
    return dest


class YOLOFaceDetector(FaceDetector):
    name = "yolo"

    def __init__(self, model_path=DEFAULT_MODEL, device=0, imgsz=640):
        from ultralytics import YOLO

        model_path = _ensure_model(model_path)

        # Prefer TensorRT engine if it exists next to the .pt
        engine = model_path.replace(".pt", ".engine")
        if not model_path.endswith(".engine") and os.path.exists(engine):
            model_path = engine
            print(f"[yolo] Using TensorRT engine: {engine}")

        self._model = YOLO(model_path, task="detect")
        self._device = device
        self._imgsz = imgsz

        if not model_path.endswith(".engine"):
            import torch
            if torch.cuda.is_available():
                self._model.to(f"cuda:{device}" if isinstance(device, int) else device)

        # Warmup
        try:
            import torch
            dummy = torch.zeros(1, 3, imgsz, imgsz).cuda()
            for _ in range(3):
                self._model(dummy, verbose=False, device=device)
            print("[yolo] Warmup done")
        except Exception:
            pass

    def detect(self, frame_bgr):
        h, w = frame_bgr.shape[:2]
        results = self._model(frame_bgr, verbose=False, device=self._device,
                              imgsz=self._imgsz)
        faces = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                bw = x2 - x1
                bh = y2 - y1
                faces.append(Detection(
                    cx=(x1 + bw / 2) / w,
                    cy=(y1 + bh / 2) / h,
                    w=bw / w,
                    h=bh / h,
                    confidence=float(box.conf),
                ))
        return faces


def create(model_path=DEFAULT_MODEL, device=0, imgsz=640, **_kwargs):
    return YOLOFaceDetector(model_path=model_path, device=device, imgsz=imgsz)
