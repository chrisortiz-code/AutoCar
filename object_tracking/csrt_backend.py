"""CSRT tracker backend — accurate bounding boxes via OpenCV's
Channel and Spatial Reliability Tracker.

Unlike feature-matching backends (ORB, SIFT, LightGlue) that use
homography projection for bounding boxes, CSRT directly tracks the
bbox frame-to-frame, producing tight, stable boxes.

No extra dependencies — included in opencv-python.
"""

import cv2
import numpy as np

from .base import ObjectMatcher, Match

# Histogram correlation threshold — below this the tracker has drifted
_HIST_CONF_MIN = 0.25


def _hsv_histogram(image_bgr):
    """Compute a normalized H-S histogram for color comparison."""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [30, 32],
                        [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist


class CSRTMatcher(ObjectMatcher):
    name = "csrt"

    def __init__(self, min_confidence=0.25):
        self._min_confidence = min_confidence
        self._tracker = None
        self._ref_hist = None

    def set_reference(self, image_bgr):
        # Store histogram for confidence checks; actual tracker init
        # happens via set_reference_roi which has the full frame.
        self._ref_hist = _hsv_histogram(image_bgr)

    def set_reference_roi(self, frame_bgr, x1, y1, x2, y2):
        crop = frame_bgr[y1:y2, x1:x2]
        self._ref_hist = _hsv_histogram(crop)
        # (Re-)create tracker each time a new target is selected
        self._tracker = cv2.TrackerCSRT_create()
        w = x2 - x1
        h = y2 - y1
        self._tracker.init(frame_bgr, (x1, y1, w, h))

    def find(self, frame_bgr) -> Match | None:
        if self._tracker is None:
            return None

        ok, bbox = self._tracker.update(frame_bgr)
        if not ok:
            return None

        bx, by, bw, bh = (int(v) for v in bbox)
        fh, fw = frame_bgr.shape[:2]

        # Clamp to frame bounds
        bx = max(0, bx)
        by = max(0, by)
        bw = min(bw, fw - bx)
        bh = min(bh, fh - by)
        if bw < 4 or bh < 4:
            return None

        # Confidence: compare tracked region histogram with reference
        roi = frame_bgr[by:by + bh, bx:bx + bw]
        roi_hist = _hsv_histogram(roi)
        confidence = cv2.compareHist(self._ref_hist, roi_hist,
                                     cv2.HISTCMP_CORREL)
        confidence = max(0.0, confidence)

        if confidence < self._min_confidence:
            return None

        cx = (bx + bw / 2) / fw
        cy = (by + bh / 2) / fh

        return Match(
            cx=cx,
            cy=cy,
            w=bw / fw,
            h=bh / fh,
            confidence=confidence,
        )

    def close(self):
        self._tracker = None


def create(min_confidence=0.25, **_kwargs):
    return CSRTMatcher(min_confidence=min_confidence)
