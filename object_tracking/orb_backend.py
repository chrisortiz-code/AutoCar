"""ORB feature matching backend.

Uses ORB keypoints + BFMatcher with homography estimation.
ORB is rotation-invariant by design (oriented FAST + rotated BRIEF).
Very fast, no GPU needed.

Rotation handling: inherent — ORB descriptors are rotation-invariant.
Extracts rotation angle from the homography matrix.
"""

import cv2
import numpy as np

from .base import ObjectMatcher, Match

MIN_MATCHES = 8


class ORBMatcher(ObjectMatcher):
    name = "orb"

    def __init__(self, n_features=1000, min_confidence=0.15):
        self._orb = cv2.ORB_create(nfeatures=n_features)
        self._bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        self._min_confidence = min_confidence
        self._ref_kp = None
        self._ref_des = None
        self._ref_shape = None

    def set_reference(self, image_bgr):
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        self._ref_kp, self._ref_des = self._orb.detectAndCompute(gray, None)
        self._ref_shape = gray.shape[:2]

    def find(self, frame_bgr):
        if self._ref_des is None or len(self._ref_kp) < MIN_MATCHES:
            return None

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        fh, fw = gray.shape[:2]
        kp, des = self._orb.detectAndCompute(gray, None)
        if des is None or len(kp) < MIN_MATCHES:
            return None

        matches = self._bf.knnMatch(self._ref_des, des, k=2)

        # Lowe's ratio test
        good = []
        for pair in matches:
            if len(pair) == 2:
                m, n = pair
                if m.distance < 0.75 * n.distance:
                    good.append(m)

        if len(good) < MIN_MATCHES:
            return None

        src_pts = np.float32([self._ref_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

        H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        if H is None:
            return None

        inliers = mask.ravel().sum()
        confidence = inliers / len(good)
        if confidence < self._min_confidence:
            return None

        rh, rw = self._ref_shape
        corners = np.float32([[0, 0], [rw, 0], [rw, rh], [0, rh]]).reshape(-1, 1, 2)
        projected = cv2.perspectiveTransform(corners, H).reshape(-1, 2)

        cx = projected[:, 0].mean() / fw
        cy = projected[:, 1].mean() / fh
        w = (projected[:, 0].max() - projected[:, 0].min()) / fw
        h = (projected[:, 1].max() - projected[:, 1].min()) / fh

        # Extract rotation from homography
        angle = -np.degrees(np.arctan2(H[1, 0], H[0, 0]))

        return Match(cx=cx, cy=cy, w=w, h=h, confidence=confidence, angle=angle)


def create(n_features=1000, min_confidence=0.15, **_kwargs):
    return ORBMatcher(n_features=n_features, min_confidence=min_confidence)
