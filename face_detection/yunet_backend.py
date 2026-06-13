"""YuNet (OpenCV Zoo) face detection backend.

Requires the ONNX model file. Download once:
    wget https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx

Tiny model (~220KB), very fast on CPU and GPU, zero extra pip deps.
"""

import os

import cv2

from .base import FaceDetector, Detection

DEFAULT_MODEL = "face_detection_yunet_2023mar.onnx"


class YuNetFaceDetector(FaceDetector):
    name = "yunet"

    def __init__(self, model_path=DEFAULT_MODEL, score_threshold=0.5,
                 backend_id=None, target_id=None):
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"YuNet model not found at {model_path!r}. Download it:\n"
                "  wget https://github.com/opencv/opencv_zoo/raw/main/models/"
                "face_detection_yunet/face_detection_yunet_2023mar.onnx"
            )
        # Auto-select backend: prefer CUDA if available
        if backend_id is None:
            backend_id = cv2.dnn.DNN_BACKEND_CUDA
        if target_id is None:
            target_id = cv2.dnn.DNN_TARGET_CUDA
        try:
            self._det = cv2.FaceDetectorYN.create(
                model_path, "", (320, 320),
                score_threshold=score_threshold,
                backend_id=backend_id,
                target_id=target_id,
            )
        except cv2.error:
            # Fall back to default CPU backend
            self._det = cv2.FaceDetectorYN.create(
                model_path, "", (320, 320),
                score_threshold=score_threshold,
            )

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
