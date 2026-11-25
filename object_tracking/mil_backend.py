"""MIL tracker backend — Multiple Instance Learning.

Built into OpenCV, no external models needed. Handles partial occlusion
reasonably well. Faster than CSRT but less precise on scale changes.
"""

import cv2
import numpy as np

from .base import ObjectMatcher, Match

_HIST_CONF_MIN = 0.20


def _hsv_histogram(image_bgr):
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist


def _relocate(frame_bgr, crop, orig_x, orig_y):
    ch, cw = crop.shape[:2]
    fh, fw = frame_bgr.shape[:2]
    pad_x, pad_y = cw, ch
    sx1 = max(0, orig_x - pad_x)
    sy1 = max(0, orig_y - pad_y)
    sx2 = min(fw, orig_x + cw + pad_x)
    sy2 = min(fh, orig_y + ch + pad_y)
    search_roi = frame_bgr[sy1:sy2, sx1:sx2]
    if search_roi.shape[0] <= ch or search_roi.shape[1] <= cw:
        return orig_x, orig_y, cw, ch
    result = cv2.matchTemplate(search_roi, crop, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < 0.4:
        return orig_x, orig_y, cw, ch
    return sx1 + max_loc[0], sy1 + max_loc[1], cw, ch


class MILMatcher(ObjectMatcher):
    name = "mil"

    def __init__(self, min_confidence=0.20):
        self._min_confidence = min_confidence
        self._tracker = None
        self._ref_hist = None

    def set_reference(self, image_bgr):
        self._ref_hist = _hsv_histogram(image_bgr)

    def set_reference_roi(self, frame_bgr, x1, y1, x2, y2):
        crop = frame_bgr[y1:y2, x1:x2]
        self._ref_hist = _hsv_histogram(crop)
        rx, ry, rw, rh = _relocate(frame_bgr, crop, x1, y1)
        self._tracker = cv2.TrackerMIL.create()
        self._tracker.init(frame_bgr, (rx, ry, rw, rh))

    def find(self, frame_bgr) -> Match | None:
        if self._tracker is None:
            return None
        ok, bbox = self._tracker.update(frame_bgr)
        if not ok:
            return None
        bx, by, bw, bh = (int(v) for v in bbox)
        fh, fw = frame_bgr.shape[:2]
        bx = max(0, bx)
        by = max(0, by)
        bw = min(bw, fw - bx)
        bh = min(bh, fh - by)
        if bw < 4 or bh < 4:
            return None
        roi = frame_bgr[by:by + bh, bx:bx + bw]
        roi_hist = _hsv_histogram(roi)
        confidence = max(0.0, cv2.compareHist(self._ref_hist, roi_hist, cv2.HISTCMP_CORREL))
        if confidence < self._min_confidence:
            return None
        cx = (bx + bw / 2) / fw
        cy = (by + bh / 2) / fh
        return Match(cx=cx, cy=cy, w=bw / fw, h=bh / fh, confidence=confidence)

    def close(self):
        self._tracker = None


def create(min_confidence=0.20, **_kwargs):
    return MILMatcher(min_confidence=min_confidence)
