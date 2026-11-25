"""DaSiamRPN tracker backend — Distractor-aware Siamese Region Proposal Network.

Deep learning tracker built into OpenCV. Very strong at single-object tracking
with scale/aspect changes. Requires 3 ONNX model files in object_tracking/models/:
  - dasiamrpn_model.onnx
  - dasiamrpn_kernel_cls1.onnx
  - dasiamrpn_kernel_r1.onnx

If missing, the backend will print download instructions.
"""

import os

import cv2
import numpy as np

from .base import ObjectMatcher, Match

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
_REQUIRED_FILES = [
    "dasiamrpn_model.onnx",
    "dasiamrpn_kernel_cls1.onnx",
    "dasiamrpn_kernel_r1.onnx",
]

_HIST_CONF_MIN = 0.20


def _check_models():
    missing = [f for f in _REQUIRED_FILES if not os.path.isfile(os.path.join(_MODEL_DIR, f))]
    return missing


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


class DaSiamRPNMatcher(ObjectMatcher):
    name = "dasiamrpn"

    def __init__(self, min_confidence=0.20):
        missing = _check_models()
        if missing:
            raise FileNotFoundError(
                f"DaSiamRPN model files missing from {_MODEL_DIR}/:\n"
                + "\n".join(f"  - {f}" for f in missing)
                + "\n\nDownload from OpenCV's model zoo or opencv_extra testdata/dnn/"
            )
        self._min_confidence = min_confidence
        self._tracker = None
        self._ref_hist = None

    def set_reference(self, image_bgr):
        self._ref_hist = _hsv_histogram(image_bgr)

    def set_reference_roi(self, frame_bgr, x1, y1, x2, y2):
        crop = frame_bgr[y1:y2, x1:x2]
        self._ref_hist = _hsv_histogram(crop)
        rx, ry, rw, rh = _relocate(frame_bgr, crop, x1, y1)

        params = cv2.TrackerDaSiamRPN_Params()
        params.model = os.path.join(_MODEL_DIR, "dasiamrpn_model.onnx")
        params.kernel_cls1 = os.path.join(_MODEL_DIR, "dasiamrpn_kernel_cls1.onnx")
        params.kernel_r1 = os.path.join(_MODEL_DIR, "dasiamrpn_kernel_r1.onnx")
        self._tracker = cv2.TrackerDaSiamRPN.create(params)
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

        score = self._tracker.getTrackingScore() if hasattr(self._tracker, 'getTrackingScore') else -1
        if score >= 0:
            confidence = max(0.0, min(1.0, score))
        else:
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
    return DaSiamRPNMatcher(min_confidence=min_confidence)
