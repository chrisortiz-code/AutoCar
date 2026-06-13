"""Unified face detection interface."""

from dataclasses import dataclass


@dataclass
class Detection:
    """A detected face with normalized coordinates (0..1)."""
    cx: float       # center x
    cy: float       # center y
    w: float        # bounding box width
    h: float        # bounding box height
    confidence: float

    @property
    def area(self):
        return self.w * self.h


class FaceDetector:
    """Base class — all backends implement detect()."""

    name: str = "base"

    def detect(self, frame_bgr) -> list[Detection]:
        raise NotImplementedError

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


BACKENDS = {
    "mediapipe":  "face_detection.mediapipe_backend",
    "yunet":      "face_detection.yunet_backend",
    "yolo":       "face_detection.yolo_backend",
    "scrfd":      "face_detection.scrfd_backend",
}


def create_detector(backend: str, **kwargs) -> FaceDetector:
    """Factory — create a detector by backend name.

    Extra kwargs are forwarded to the backend constructor.
    Common kwargs:
        model_path: str   — path to model weights/engine
    """
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}, choose from {list(BACKENDS)}")
    import importlib
    mod = importlib.import_module(BACKENDS[backend])
    return mod.create(**kwargs)
