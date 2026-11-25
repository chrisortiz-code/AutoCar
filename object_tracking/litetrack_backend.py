"""LiteTrack B9 backend — high-accuracy ViT tracker via ONNX Runtime.

Two-model architecture: template encoder runs once per selection,
search tracker runs every frame. Requires:
  - litetrack_template.onnx (~246MB) in object_tracking/models/
  - litetrack_search.onnx (~271MB) in object_tracking/models/
"""

import math
import os

import cv2
import numpy as np

from .base import ObjectMatcher, Match

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
_TEMPLATE_MODEL = "litetrack_template.onnx"
_SEARCH_MODEL = "litetrack_search.onnx"

_TEMPLATE_SIZE = 128
_SEARCH_SIZE = 256
_TEMPLATE_FACTOR = 2.0
_SEARCH_FACTOR = 4.0
_STRIDE = 16
_FEAT_SIZE = _SEARCH_SIZE // _STRIDE  # 16

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _sample_target(image, bbox_xywh, factor, output_sz):
    x, y, w, h = bbox_xywh
    cx, cy = x + w * 0.5, y + h * 0.5
    crop_sz = math.ceil(math.sqrt(w * h) * factor)
    if crop_sz < 1:
        crop_sz = 1
    x1 = int(round(cx - crop_sz * 0.5))
    y1 = int(round(cy - crop_sz * 0.5))
    x2 = x1 + crop_sz
    y2 = y1 + crop_sz

    ih, iw = image.shape[:2]
    pad_left = max(0, -x1)
    pad_top = max(0, -y1)
    pad_right = max(0, x2 - iw)
    pad_bottom = max(0, y2 - ih)

    cx1, cy1 = max(0, x1), max(0, y1)
    cx2, cy2 = min(iw, x2), min(ih, y2)
    crop = image[cy1:cy2, cx1:cx2]

    if pad_left > 0 or pad_top > 0 or pad_right > 0 or pad_bottom > 0:
        crop = cv2.copyMakeBorder(crop, pad_top, pad_bottom, pad_left, pad_right,
                                  cv2.BORDER_CONSTANT, value=0)

    resized = cv2.resize(crop, (output_sz, output_sz))
    resize_factor = output_sz / crop_sz
    return resized, resize_factor


def _preprocess(image_bgr):
    img = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    img = img.astype(np.float32) / 255.0
    img = (img - _MEAN) / _STD
    return img.transpose(2, 0, 1)[np.newaxis]


def _hann2d(size):
    h = np.outer(np.hanning(size), np.hanning(size)).astype(np.float32)
    return h


def _clip_box(box, h, w, margin=10):
    x, y, bw, bh = box
    x = max(0, min(x, w - margin))
    y = max(0, min(y, h - margin))
    bw = max(margin, min(bw, w - x))
    bh = max(margin, min(bh, h - y))
    return [x, y, bw, bh]


class LiteTrackMatcher(ObjectMatcher):
    name = "litetrack"

    def __init__(self):
        t_path = os.path.join(_MODEL_DIR, _TEMPLATE_MODEL)
        s_path = os.path.join(_MODEL_DIR, _SEARCH_MODEL)
        for p, n in [(t_path, _TEMPLATE_MODEL), (s_path, _SEARCH_MODEL)]:
            if not os.path.isfile(p):
                raise FileNotFoundError(
                    f"LiteTrack model missing: {n}\n"
                    "Export from PyTorch weights using export_litetrack.py"
                )

        import onnxruntime as ort
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self._template_session = ort.InferenceSession(t_path, providers=providers)
        self._search_session = ort.InferenceSession(s_path, providers=providers)
        self._hann = _hann2d(_FEAT_SIZE)
        self._z_feat = None
        self._state = None
        self._fh = 0
        self._fw = 0

    def set_reference(self, image_bgr):
        pass

    def set_reference_roi(self, frame_bgr, x1, y1, x2, y2):
        self._fh, self._fw = frame_bgr.shape[:2]
        w, h = x2 - x1, y2 - y1
        self._state = [float(x1), float(y1), float(w), float(h)]

        crop, _ = _sample_target(frame_bgr, self._state, _TEMPLATE_FACTOR, _TEMPLATE_SIZE)
        template_input = _preprocess(crop)

        # template_bb is xyxy normalized to crop coordinates
        # In the crop, the target is roughly centered
        sx, sy, sw, sh = self._state
        crop_sz = math.ceil(math.sqrt(sw * sh) * _TEMPLATE_FACTOR)
        cx_in_crop = (sx + sw * 0.5 - (sx + sw * 0.5 - crop_sz * 0.5)) / crop_sz
        cy_in_crop = (sy + sh * 0.5 - (sy + sh * 0.5 - crop_sz * 0.5)) / crop_sz
        w_in_crop = sw / crop_sz
        h_in_crop = sh / crop_sz
        # xyxy format
        bb_x1 = cx_in_crop - w_in_crop * 0.5
        bb_y1 = cy_in_crop - h_in_crop * 0.5
        bb_x2 = cx_in_crop + w_in_crop * 0.5
        bb_y2 = cy_in_crop + h_in_crop * 0.5
        template_bb = np.array([[bb_x1, bb_y1, bb_x2, bb_y2]], dtype=np.float32)

        self._z_feat = self._template_session.run(
            None,
            {"template": template_input, "template_bb": template_bb}
        )[0]

    def find(self, frame_bgr) -> Match | None:
        if self._state is None or self._z_feat is None:
            return None

        self._fh, self._fw = frame_bgr.shape[:2]
        search_crop, resize_factor = _sample_target(
            frame_bgr, self._state, _SEARCH_FACTOR, _SEARCH_SIZE
        )
        search_input = _preprocess(search_crop)

        score_map, size_map, offset_map = self._search_session.run(
            None,
            {"z_feat": self._z_feat, "search": search_input}
        )

        # score_map: [1,1,16,16], size_map: [1,2,16,16], offset_map: [1,2,16,16]
        score = score_map[0, 0]  # [16,16]
        response = self._hann * score
        idx = np.argmax(response)
        idx_y, idx_x = divmod(int(idx), _FEAT_SIZE)

        # Get size and offset at best location
        s = size_map[0, :, idx_y, idx_x]  # [2]
        o = offset_map[0, :, idx_y, idx_x]  # [2]

        # cx, cy, w, h in normalized search-crop coordinates
        cx = (idx_x + o[0]) / _FEAT_SIZE
        cy = (idx_y + o[1]) / _FEAT_SIZE
        w = s[0]
        h = s[1]

        # Convert to pixel coordinates in search crop
        cx_px = cx * _SEARCH_SIZE / resize_factor
        cy_px = cy * _SEARCH_SIZE / resize_factor
        w_px = w * _SEARCH_SIZE / resize_factor
        h_px = h * _SEARCH_SIZE / resize_factor

        # Map back to original image coordinates
        prev_cx = self._state[0] + 0.5 * self._state[2]
        prev_cy = self._state[1] + 0.5 * self._state[3]
        half_side = 0.5 * _SEARCH_SIZE / resize_factor

        real_cx = cx_px + (prev_cx - half_side)
        real_cy = cy_px + (prev_cy - half_side)

        new_state = [real_cx - w_px * 0.5, real_cy - h_px * 0.5, w_px, h_px]
        new_state = _clip_box(new_state, self._fh, self._fw)
        self._state = new_state

        confidence = float(np.max(score))
        confidence = max(0.0, min(1.0, confidence))

        x, y, bw, bh = self._state
        return Match(
            cx=(x + bw / 2) / self._fw,
            cy=(y + bh / 2) / self._fh,
            w=bw / self._fw,
            h=bh / self._fh,
            confidence=confidence,
        )

    def close(self):
        self._template_session = None
        self._search_session = None
        self._z_feat = None
        self._state = None


def create(**_kwargs):
    return LiteTrackMatcher()
