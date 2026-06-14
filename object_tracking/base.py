"""Unified interface for reference-image object matching."""

from dataclasses import dataclass


@dataclass
class Match:
    """A matched object location with normalized coordinates (0..1)."""
    cx: float           # center x
    cy: float           # center y
    w: float            # bounding box width
    h: float            # bounding box height
    confidence: float   # match quality 0..1
    angle: float = 0.0  # estimated rotation in degrees


class ObjectMatcher:
    """Base class — all backends implement set_reference() and find()."""

    name: str = "base"

    def set_reference(self, image_bgr):
        """Set the reference image (tight crop of target object)."""
        raise NotImplementedError

    def find(self, frame_bgr) -> Match | None:
        """Find the reference object in the frame. Returns best match or None."""
        raise NotImplementedError

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


BACKENDS = {
    "template":  "object_tracking.template_backend",
    "orb":       "object_tracking.orb_backend",
    "akaze":     "object_tracking.akaze_backend",
    "sift":      "object_tracking.sift_backend",
}


def create_matcher(backend: str, **kwargs) -> ObjectMatcher:
    """Factory — create a matcher by backend name."""
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}, choose from {list(BACKENDS)}")
    import importlib
    mod = importlib.import_module(BACKENDS[backend])
    return mod.create(**kwargs)
