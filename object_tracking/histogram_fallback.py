"""Color-blob fallback matcher.

When the primary backend returns None, this samples the median HSV color
from the center of the reference crop, then uses cv2.inRange to find the
largest blob of that color in the frame.  Handles hue wrapping (red).

Based on the old cam_sender.py color-tracking approach: fast, reliable
for solid-color objects, sub-millisecond on Jetson.
"""

import cv2
import numpy as np

from .base import Match

# HSV tolerance around the sampled color
HSV_TOL_H = 30
HSV_TOL_S = 80
HSV_TOL_V = 80

# minimum contour area in pixels to count as a detection
MIN_BLOB = 300

# morphology kernel for noise removal
_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))


class ColorBlobFallback:
    """Samples center color from reference, finds largest matching blob."""

    def __init__(self):
        self._hsv_center = None  # (H, S, V) of target color

    def set_reference(self, image_bgr):
        h, w = image_bgr.shape[:2]
        # sample the center 40% of the crop to avoid edge/background
        margin_y, margin_x = int(h * 0.3), int(w * 0.3)
        center_region = image_bgr[margin_y:h - margin_y, margin_x:w - margin_x]
        if center_region.size == 0:
            center_region = image_bgr
        hsv = cv2.cvtColor(center_region, cv2.COLOR_BGR2HSV)
        # median is more robust than mean to outlier pixels
        self._hsv_center = (
            int(np.median(hsv[:, :, 0])),
            int(np.median(hsv[:, :, 1])),
            int(np.median(hsv[:, :, 2])),
        )

    def find_best(self, frame_bgr) -> Match | None:
        if self._hsv_center is None:
            return None

        fh, fw = frame_bgr.shape[:2]
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        h, s, v = self._hsv_center

        lo_s, hi_s = max(0, s - HSV_TOL_S), min(255, s + HSV_TOL_S)
        lo_v, hi_v = max(0, v - HSV_TOL_V), min(255, v + HSV_TOL_V)

        # handle hue wrapping (red straddles 0/180)
        if h - HSV_TOL_H < 0:
            m1 = cv2.inRange(hsv, np.array([0, lo_s, lo_v]),
                             np.array([h + HSV_TOL_H, hi_s, hi_v]))
            m2 = cv2.inRange(hsv, np.array([180 + h - HSV_TOL_H, lo_s, lo_v]),
                             np.array([179, hi_s, hi_v]))
            mask = cv2.bitwise_or(m1, m2)
        elif h + HSV_TOL_H > 179:
            m1 = cv2.inRange(hsv, np.array([h - HSV_TOL_H, lo_s, lo_v]),
                             np.array([179, hi_s, hi_v]))
            m2 = cv2.inRange(hsv, np.array([0, lo_s, lo_v]),
                             np.array([h + HSV_TOL_H - 180, hi_s, hi_v]))
            mask = cv2.bitwise_or(m1, m2)
        else:
            mask = cv2.inRange(hsv,
                               np.array([h - HSV_TOL_H, lo_s, lo_v]),
                               np.array([h + HSV_TOL_H, hi_s, hi_v]))

        # morphology to remove noise
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _KERNEL)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _KERNEL)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        biggest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(biggest)
        if area < MIN_BLOB:
            return None

        M = cv2.moments(biggest)
        if M["m00"] == 0:
            return None

        cx = (M["m10"] / M["m00"]) / fw
        cy = (M["m01"] / M["m00"]) / fh
        x, y, bw, bh = cv2.boundingRect(biggest)

        # confidence = fraction of bounding box filled by the blob
        confidence = area / max(bw * bh, 1)

        return Match(
            cx=cx, cy=cy,
            w=bw / fw,
            h=bh / fh,
            confidence=confidence,
            angle=0.0,
            fallback=True,
        )
