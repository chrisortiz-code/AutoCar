"""Template matching backend (cv2.matchTemplate).

Multi-scale + multi-rotation search. Tests rotated versions of the
template at multiple scales to find the best match.

Rotation handling: brute-force — rotates template at fixed angle steps.
Fast per-angle but O(scales * angles), so gets expensive with fine steps.
"""

import cv2
import numpy as np

from .base import ObjectMatcher, Match


class TemplateMatcher(ObjectMatcher):
    name = "template"

    def __init__(self, scales=None, angle_step=15, method=cv2.TM_CCOEFF_NORMED,
                 min_confidence=0.45):
        self._scales = scales or [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.4]
        self._angle_step = angle_step
        self._angles = list(range(0, 360, angle_step))
        self._method = method
        self._min_confidence = min_confidence
        self._ref = None
        self._ref_gray = None

    def set_reference(self, image_bgr):
        self._ref = image_bgr.copy()
        self._ref_gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    def _rotate_image(self, image, angle):
        h, w = image.shape[:2]
        center = (w / 2, h / 2)
        mat = cv2.getRotationMatrix2D(center, angle, 1.0)
        cos = abs(mat[0, 0])
        sin = abs(mat[0, 1])
        nw = int(h * sin + w * cos)
        nh = int(h * cos + w * sin)
        mat[0, 2] += (nw / 2) - center[0]
        mat[1, 2] += (nh / 2) - center[1]
        return cv2.warpAffine(image, mat, (nw, nh),
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    def find(self, frame_bgr):
        if self._ref_gray is None:
            return None
        frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        fh, fw = frame_gray.shape[:2]

        best_val = -1
        best_loc = None
        best_tw = 0
        best_th = 0
        best_angle = 0

        for angle in self._angles:
            if angle == 0:
                rotated = self._ref_gray
            else:
                rotated = self._rotate_image(self._ref_gray, angle)

            for scale in self._scales:
                th = int(rotated.shape[0] * scale)
                tw = int(rotated.shape[1] * scale)
                if tw < 10 or th < 10 or tw >= fw or th >= fh:
                    continue
                resized = cv2.resize(rotated, (tw, th))
                result = cv2.matchTemplate(frame_gray, resized, self._method)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)

                if max_val > best_val:
                    best_val = max_val
                    best_loc = max_loc
                    best_tw = tw
                    best_th = th
                    best_angle = angle

        if best_val < self._min_confidence or best_loc is None:
            return None

        cx = (best_loc[0] + best_tw / 2) / fw
        cy = (best_loc[1] + best_th / 2) / fh
        return Match(cx=cx, cy=cy, w=best_tw / fw, h=best_th / fh,
                     confidence=best_val, angle=best_angle)


def create(angle_step=15, min_confidence=0.45, **_kwargs):
    return TemplateMatcher(angle_step=angle_step, min_confidence=min_confidence)
