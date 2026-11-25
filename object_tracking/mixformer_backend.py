"""MixFormerV2 backend — high-accuracy transformer tracker via ONNX Runtime.

Requires mixformer_v2_sim.onnx (~62MB) in object_tracking/models/.
Uses dual-template (initial + online) with periodic online update.
"""

import math
import os

import cv2
import numpy as np

from .base import ObjectMatcher, Match

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
_MODEL_FILE = "mixformer_v2_sim.onnx"

_SEARCH_SIZE = 224
_TEMPLATE_SIZE = 112
_SEARCH_FACTOR = 4.5
_TEMPLATE_FACTOR = 2.0
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_UPDATE_INTERVAL = 200
_CONF_THRESHOLD = 0.5


def _sample_target(image, bbox_xywh, factor, output_sz):
    """Crop and resize a square region around bbox, zero-padding if needed."""
    x, y, w, h = bbox_xywh
    cx, cy = x + w * 0.5, y + h * 0.5
    crop_sz = math.ceil(math.sqrt(w * h) * factor)
    x1 = int(round(cx - crop_sz * 0.5))
    y1 = int(round(cy - crop_sz * 0.5))
    x2 = x1 + crop_sz
    y2 = y1 + crop_sz

    ih, iw = image.shape[:2]
    pad_left = max(0, -x1)
    pad_top = max(0, -y1)
    pad_right = max(0, x2 - iw)
    pad_bottom = max(0, y2 - ih)

    cx1 = max(0, x1)
    cy1 = max(0, y1)
    cx2 = min(iw, x2)
    cy2 = min(ih, y2)
    crop = image[cy1:cy2, cx1:cx2]

    if pad_left > 0 or pad_top > 0 or pad_right > 0 or pad_bottom > 0:
        crop = cv2.copyMakeBorder(crop, pad_top, pad_bottom, pad_left, pad_right,
                                  cv2.BORDER_CONSTANT, value=0)

    resized = cv2.resize(crop, (output_sz, output_sz))
    resize_factor = output_sz / crop_sz
    return resized, resize_factor


def _preprocess(image_bgr, output_sz):
    """BGR uint8 -> NCHW float32 with ImageNet normalization."""
    img = image_bgr.astype(np.float32) / 255.0
    img = (img - _MEAN) / _STD
    img = img.transpose(2, 0, 1)[np.newaxis]
    return img


def _clip_box(box, h, w, margin=10):
    x, y, bw, bh = box
    x = max(0, min(x, w - margin))
    y = max(0, min(y, h - margin))
    bw = max(margin, min(bw, w - x))
    bh = max(margin, min(bh, h - y))
    return [x, y, bw, bh]


class MixFormerMatcher(ObjectMatcher):
    name = "mixformer"

    def __init__(self):
        model_path = os.path.join(_MODEL_DIR, _MODEL_FILE)
        if not os.path.isfile(model_path):
            raise FileNotFoundError(
                f"MixFormerV2 model missing: {model_path}\n"
                "Download from https://huggingface.co/AXERA-TECH/MixFormerV2"
            )

        import onnxruntime as ort
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self._session = ort.InferenceSession(model_path, providers=providers)
        self._input_names = [inp.name for inp in self._session.get_inputs()]
        self._state = None
        self._template = None
        self._online_template = None
        self._online_max_template = None
        self._max_pred_score = -1.0
        self._frame_count = 0
        self._fh = 0
        self._fw = 0

    def set_reference(self, image_bgr):
        pass

    def set_reference_roi(self, frame_bgr, x1, y1, x2, y2):
        self._fh, self._fw = frame_bgr.shape[:2]
        w, h = x2 - x1, y2 - y1
        self._state = [float(x1), float(y1), float(w), float(h)]

        crop, _ = _sample_target(frame_bgr, self._state, _TEMPLATE_FACTOR, _TEMPLATE_SIZE)
        self._template = _preprocess(crop, _TEMPLATE_SIZE)
        self._online_template = self._template.copy()
        self._online_max_template = self._template.copy()
        self._max_pred_score = -1.0
        self._frame_count = 0

    def find(self, frame_bgr) -> Match | None:
        if self._state is None or self._template is None:
            return None

        self._fh, self._fw = frame_bgr.shape[:2]
        search_crop, resize_factor = _sample_target(
            frame_bgr, self._state, _SEARCH_FACTOR, _SEARCH_SIZE
        )
        search_input = _preprocess(search_crop, _SEARCH_SIZE)

        feed = {}
        for name in self._input_names:
            if "search" in name.lower() or "img_search" in name.lower():
                feed[name] = search_input
            elif "img_ot" in name.lower() or "online" in name.lower():
                feed[name] = self._online_template
            else:
                feed[name] = self._template

        if len(feed) != len(self._input_names):
            for i, name in enumerate(self._input_names):
                if name not in feed:
                    if i == 0:
                        feed[name] = self._template
                    elif i == 1:
                        feed[name] = self._online_template
                    else:
                        feed[name] = search_input

        ort_outs = self._session.run(None, feed)
        pred_boxes = ort_outs[0]
        pred_score = float(ort_outs[1]) if np.ndim(ort_outs[1]) == 0 else float(np.mean(ort_outs[1]))

        if np.ndim(pred_boxes) > 1:
            pred_box = np.mean(pred_boxes, axis=0)
        else:
            pred_box = pred_boxes
        pred_box = pred_box * _SEARCH_SIZE / resize_factor

        cx_prev = self._state[0] + 0.5 * self._state[2]
        cy_prev = self._state[1] + 0.5 * self._state[3]
        half_side = 0.5 * _SEARCH_SIZE / resize_factor

        cx_real = pred_box[0] + (cx_prev - half_side)
        cy_real = pred_box[1] + (cy_prev - half_side)
        w_real = pred_box[2]
        h_real = pred_box[3]

        new_state = [cx_real - 0.5 * w_real, cy_real - 0.5 * h_real, w_real, h_real]
        new_state = _clip_box(new_state, self._fh, self._fw)
        self._state = new_state

        self._frame_count += 1
        if pred_score > _CONF_THRESHOLD and pred_score > self._max_pred_score:
            self._max_pred_score = pred_score
            crop, _ = _sample_target(frame_bgr, self._state, _TEMPLATE_FACTOR, _TEMPLATE_SIZE)
            self._online_max_template = _preprocess(crop, _TEMPLATE_SIZE)

        if self._frame_count % _UPDATE_INTERVAL == 0:
            self._online_template = self._online_max_template.copy()
            self._max_pred_score = -1.0
            crop, _ = _sample_target(frame_bgr, self._state, _TEMPLATE_FACTOR, _TEMPLATE_SIZE)
            self._online_max_template = _preprocess(crop, _TEMPLATE_SIZE)

        confidence = max(0.0, min(1.0, pred_score))
        x, y, w, h = self._state
        return Match(
            cx=(x + w / 2) / self._fw,
            cy=(y + h / 2) / self._fh,
            w=w / self._fw,
            h=h / self._fh,
            confidence=confidence,
        )

    def close(self):
        self._session = None
        self._state = None
        self._template = None
        self._online_template = None


def create(**_kwargs):
    return MixFormerMatcher()
