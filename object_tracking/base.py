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
    fallback: bool = False  # True when result came from fallback path


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


class WithFallback(ObjectMatcher):
    """Wrapper: runs primary backend, falls back to histogram cosine
    similarity when the primary returns None.  Always produces a Match."""

    def __init__(self, primary: ObjectMatcher):
        from .histogram_fallback import HistogramFallback
        self._primary = primary
        self._fallback = HistogramFallback()
        self.name = primary.name

    def set_reference(self, image_bgr):
        self._primary.set_reference(image_bgr)
        h, w = image_bgr.shape[:2]
        self._fallback.set_reference(image_bgr)

    def find(self, frame_bgr) -> Match | None:
        result = self._primary.find(frame_bgr)
        if result is not None:
            return result
        return self._fallback.find_best(frame_bgr)

    def close(self):
        self._primary.close()


def create_matcher(backend: str, *, fallback: bool = True, **kwargs) -> ObjectMatcher:
    """Factory — create a matcher by backend name.

    Args:
        fallback: wrap with histogram cosine-similarity fallback so that
                  find() always returns a Match (default True).
    """
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}, choose from {list(BACKENDS)}")
    import importlib
    mod = importlib.import_module(BACKENDS[backend])
    matcher = mod.create(**kwargs)
    if fallback:
        matcher = WithFallback(matcher)
    return matcher
