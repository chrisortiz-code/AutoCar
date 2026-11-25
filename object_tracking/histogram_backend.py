"""Multi-scale histogram sliding-window matcher.

Computes a normalized HSV histogram of the reference crop, then slides
windows at multiple scales across the frame, scoring each by histogram
correlation. Scale-invariant because histograms are normalized to
percentages. Fast on CPU — no deep models needed.
"""

import cv2
import numpy as np

from .base import ObjectMatcher, Match

_H_BINS = 30
_S_BINS = 32
_HIST_RANGES = [0, 180, 0, 256]
_CHANNELS = [0, 1]

_STRIDE_RATIO = 0.25
_SCALES = [0.5, 0.7, 1.0, 1.4, 2.0]
_MIN_WINDOW = 24
_MIN_CONFIDENCE = 0.35


def _calc_hist(image_bgr):
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], _CHANNELS, None,
                        [_H_BINS, _S_BINS], _HIST_RANGES)
    cv2.normalize(hist, hist)
    return hist


class HistogramMatcher(ObjectMatcher):
    name = "histogram"

    def __init__(self, min_confidence=_MIN_CONFIDENCE):
        self._min_confidence = min_confidence
        self._ref_hist = None
        self._ref_w = 0
        self._ref_h = 0

    def set_reference(self, image_bgr):
        self._ref_hist = _calc_hist(image_bgr)
        self._ref_h, self._ref_w = image_bgr.shape[:2]

    def find(self, frame_bgr) -> Match | None:
        if self._ref_hist is None:
            return None

        fh, fw = frame_bgr.shape[:2]
        hsv_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

        best_score = -1.0
        best_box = None

        for scale in _SCALES:
            ww = max(_MIN_WINDOW, int(self._ref_w * scale))
            wh = max(_MIN_WINDOW, int(self._ref_h * scale))

            if ww > fw or wh > fh:
                continue

            stride_x = max(4, int(ww * _STRIDE_RATIO))
            stride_y = max(4, int(wh * _STRIDE_RATIO))

            for y in range(0, fh - wh + 1, stride_y):
                for x in range(0, fw - ww + 1, stride_x):
                    roi = hsv_frame[y:y + wh, x:x + ww]
                    hist = cv2.calcHist([roi], _CHANNELS, None,
                                       [_H_BINS, _S_BINS], _HIST_RANGES)
                    cv2.normalize(hist, hist)
                    score = cv2.compareHist(self._ref_hist, hist,
                                           cv2.HISTCMP_CORREL)
                    if score > best_score:
                        best_score = score
                        best_box = (x, y, ww, wh)

        if best_score < self._min_confidence or best_box is None:
            return None

        bx, by, bw, bh = best_box
        return Match(
            cx=(bx + bw / 2) / fw,
            cy=(by + bh / 2) / fh,
            w=bw / fw,
            h=bh / fh,
            confidence=best_score,
        )

    def close(self):
        self._ref_hist = None


def create(min_confidence=_MIN_CONFIDENCE, **_kwargs):
    return HistogramMatcher(min_confidence=min_confidence)
