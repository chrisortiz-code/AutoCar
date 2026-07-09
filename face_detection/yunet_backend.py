"""YuNet (OpenCV Zoo) face detection backend.

Requires the ONNX model file. Download once:
    wget https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx

Tiny model (~220KB), very fast on CPU and GPU, zero extra pip deps.
"""

import os
import urllib.request

import cv2
import numpy as np

from .base import FaceDetector, Detection
from models import MODELS_DIR

DEFAULT_MODEL = "face_detection_yunet_2023mar.onnx"
MODEL_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")


def _ensure_model(model_path):
    if os.path.exists(model_path):
        return model_path
    dest = os.path.join(MODELS_DIR, os.path.basename(model_path))
    if os.path.exists(dest):
        return dest
    print(f"[yunet] Downloading {os.path.basename(model_path)} to models/ ...")
    urllib.request.urlretrieve(MODEL_URL, dest)
    print(f"[yunet] Saved to {dest}")
    return dest


class YuNetFaceDetector(FaceDetector):
    name = "yunet"

    def __init__(self, model_path=DEFAULT_MODEL, score_threshold=0.5,
                 backend_id=None, target_id=None):
        model_path = _ensure_model(model_path)
        # Try backends in order: caller-specified, then CUDA, then CPU
        attempts = []
        if backend_id is not None and target_id is not None:
            attempts.append((backend_id, target_id, "custom"))
        attempts.append((cv2.dnn.DNN_BACKEND_CUDA, cv2.dnn.DNN_TARGET_CUDA, "CUDA"))
        attempts.append((cv2.dnn.DNN_BACKEND_DEFAULT, cv2.dnn.DNN_TARGET_CPU, "CPU"))

        for bid, tid, label in attempts:
            try:
                det = cv2.FaceDetectorYN.create(
                    model_path, "", (320, 320),
                    score_threshold=score_threshold,
                    backend_id=bid,
                    target_id=tid,
                )
                # Test with a dummy frame to confirm it actually works
                dummy = np.zeros((320, 320, 3), dtype=np.uint8)
                det.setInputSize((320, 320))
                det.detect(dummy)
                self._det = det
                print(f"[yunet] Using {label} backend")
                return
            except cv2.error:
                continue
        raise RuntimeError("YuNet: no working DNN backend found")

    def detect(self, frame_bgr):
        h, w = frame_bgr.shape[:2]
        self._det.setInputSize((w, h))
        _, raw = self._det.detect(frame_bgr)
        faces = []
        if raw is not None:
            for r in raw:
                x, y, bw, bh = float(r[0]), float(r[1]), float(r[2]), float(r[3])
                conf = float(r[14])
                faces.append(Detection(
                    cx=(x + bw / 2) / w,
                    cy=(y + bh / 2) / h,
                    w=bw / w,
                    h=bh / h,
                    confidence=conf,
                ))
        return faces


def create(model_path=DEFAULT_MODEL, score_threshold=0.5, **_kwargs):
    return YuNetFaceDetector(model_path=model_path,
                             score_threshold=score_threshold)
