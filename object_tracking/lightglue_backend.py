"""LightGlue + SuperPoint feature matching backend.

State-of-the-art learned feature matcher — dramatically more reliable than
ORB/SIFT/AKAZE for viewpoint changes, lighting variation, and low-texture
objects.  GPU-accelerated via PyTorch.

Install:  pip install lightglue torch torchvision

Ref: https://github.com/cvg/LightGlue
"""

import cv2
import numpy as np
import torch

from .base import ObjectMatcher, Match

MIN_MATCHES = 8
# Resize longest edge to this before extraction (saves GPU memory / time)
MAX_EDGE = 640


def _to_gray_tensor(image_bgr, device, max_edge=MAX_EDGE):
    """Convert BGR uint8 image to float32 [1,1,H,W] tensor, optionally down-scaled."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    scale = 1.0
    if max(h, w) > max_edge:
        scale = max_edge / max(h, w)
        gray = cv2.resize(gray, (int(w * scale), int(h * scale)),
                          interpolation=cv2.INTER_AREA)
    tensor = torch.from_numpy(gray).float() / 255.0
    tensor = tensor.unsqueeze(0).unsqueeze(0).to(device)  # [1,1,H,W]
    return tensor, scale


class LightGlueMatcher(ObjectMatcher):
    name = "lightglue"

    def __init__(self, max_num_keypoints=1024, min_confidence=0.15, device=None):
        from lightglue import LightGlue, SuperPoint

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = device
        self._min_confidence = min_confidence
        self._max_edge = MAX_EDGE

        self._extractor = SuperPoint(
            max_num_keypoints=max_num_keypoints,
        ).eval().to(device)

        self._matcher = LightGlue(
            features="superpoint",
        ).eval().to(device)

        self._ref_feats = None
        self._ref_shape = None  # (h, w) of original image

        # Warm up (first call is slow due to JIT / memory allocation)
        dummy = torch.zeros(1, 1, 64, 64, device=device)
        with torch.no_grad():
            self._extractor.extract(dummy)

    # ------------------------------------------------------------------ #

    def set_reference(self, image_bgr):
        self._ref_shape = image_bgr.shape[:2]
        tensor, _ = _to_gray_tensor(image_bgr, self._device, self._max_edge)
        with torch.no_grad():
            self._ref_feats = self._extractor.extract(tensor)

    def find(self, frame_bgr) -> Match | None:
        if self._ref_feats is None:
            return None
        ref_kp_count = self._ref_feats["keypoints"].shape[1]
        if ref_kp_count < MIN_MATCHES:
            return None

        fh, fw = frame_bgr.shape[:2]
        tensor, scale = _to_gray_tensor(frame_bgr, self._device, self._max_edge)

        with torch.no_grad():
            frame_feats = self._extractor.extract(tensor)
            result = self._matcher({"image0": self._ref_feats, "image1": frame_feats})

        matches_idx = result["matches"][0].cpu().numpy()  # (N, 2)
        if len(matches_idx) < MIN_MATCHES:
            return None

        ref_kp = self._ref_feats["keypoints"][0].cpu().numpy()
        frm_kp = frame_feats["keypoints"][0].cpu().numpy()

        src_pts = ref_kp[matches_idx[:, 0]]
        dst_pts = frm_kp[matches_idx[:, 1]]

        # Scale keypoints back to original reference image coords
        rh, rw = self._ref_shape
        ref_scale = 1.0
        if max(rh, rw) > self._max_edge:
            ref_scale = self._max_edge / max(rh, rw)
        src_pts = src_pts / ref_scale

        # Scale frame keypoints back to original frame coords
        dst_pts = dst_pts / scale

        src_pts = src_pts.reshape(-1, 1, 2).astype(np.float32)
        dst_pts = dst_pts.reshape(-1, 1, 2).astype(np.float32)

        H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        if H is None:
            return None

        inliers = mask.ravel().sum()
        confidence = inliers / len(matches_idx)
        if confidence < self._min_confidence:
            return None

        corners = np.float32([[0, 0], [rw, 0], [rw, rh], [0, rh]]).reshape(-1, 1, 2)
        projected = cv2.perspectiveTransform(corners, H).reshape(-1, 2)

        cx = projected[:, 0].mean() / fw
        cy = projected[:, 1].mean() / fh
        w = (projected[:, 0].max() - projected[:, 0].min()) / fw
        h = (projected[:, 1].max() - projected[:, 1].min()) / fh

        angle = -np.degrees(np.arctan2(H[1, 0], H[0, 0]))

        return Match(cx=cx, cy=cy, w=w, h=h, confidence=confidence, angle=angle)

    def close(self):
        self._ref_feats = None

    # ------------------------------------------------------------------ #


def create(max_num_keypoints=1024, min_confidence=0.15, device=None, **_kwargs):
    return LightGlueMatcher(
        max_num_keypoints=max_num_keypoints,
        min_confidence=min_confidence,
        device=device,
    )
