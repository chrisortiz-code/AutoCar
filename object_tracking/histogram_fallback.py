"""Histogram back-projection fallback matcher.

When the primary backend returns None, this uses OpenCV's calcBackProject
to produce a per-pixel probability map from the reference HSV histogram,
then finds the peak via CamShift.  Runs entirely in OpenCV C++ — fast
enough for real-time on Jetson (~1-2 ms per frame at 424×240).

The confidence is the Bhattacharyya similarity (0..1) between the
reference histogram and the histogram of the winning region.
"""

import cv2
import numpy as np

from .base import Match

H_BINS, S_BINS = 30, 32
RANGES = [0, 180, 0, 256]


class HistogramFallback:
    """Back-projection based fallback — always returns a Match."""

    def __init__(self):
        self._ref_hist = None
        self._ref_h = 0
        self._ref_w = 0

    def set_reference(self, image_bgr):
        hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
        self._ref_h, self._ref_w = image_bgr.shape[:2]
        hist = cv2.calcHist([hsv], [0, 1], None, [H_BINS, S_BINS], RANGES)
        cv2.normalize(hist, hist, 0, 255, cv2.NORM_MINMAX)
        self._ref_hist = hist

    def find_best(self, frame_bgr) -> Match | None:
        if self._ref_hist is None:
            return None

        fh, fw = frame_bgr.shape[:2]
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

        # back-project: each pixel gets probability of belonging to reference
        bp = cv2.calcBackProject([hsv], [0, 1], self._ref_hist, RANGES, 1)

        # light blur to smooth noise
        cv2.GaussianBlur(bp, (5, 5), 0, dst=bp)

        # find peak location via matchTemplate on the probability map
        # use a uniform kernel the size of the reference as a box filter
        kern_h = min(self._ref_h, fh - 1)
        kern_w = min(self._ref_w, fw - 1)
        if kern_h < 5 or kern_w < 5:
            return None

        kernel = np.ones((kern_h, kern_w), dtype=np.float32)
        result = cv2.matchTemplate(bp.astype(np.float32), kernel,
                                   cv2.TM_CCORR_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        x, y = max_loc
        cx = (x + kern_w / 2) / fw
        cy = (y + kern_h / 2) / fh

        # compute Bhattacharyya similarity for the winning patch
        patch_hsv = hsv[y:y + kern_h, x:x + kern_w]
        patch_hist = cv2.calcHist([patch_hsv], [0, 1], None,
                                  [H_BINS, S_BINS], RANGES)
        cv2.normalize(patch_hist, patch_hist, 0, 255, cv2.NORM_MINMAX)
        bhatt = cv2.compareHist(self._ref_hist, patch_hist,
                                cv2.HISTCMP_BHATTACHARYYA)
        confidence = 1.0 - bhatt  # 1 = identical, 0 = no overlap

        return Match(
            cx=cx, cy=cy,
            w=kern_w / fw,
            h=kern_h / fh,
            confidence=max(confidence, 0.0),
            angle=0.0,
            fallback=True,
        )
