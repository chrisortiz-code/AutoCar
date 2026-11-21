"""RL policy inference runner for real-world deployment.

Supports depth-only and RGBD model variants. Target identification is handled
by CSRT tracking outside the policy, with tracker-derived signals passed
through the state vector.

Automatically uses TorchScript FP16 exports when available (run
export_jetson.py to generate them). Falls back to raw checkpoints otherwise.
"""

from __future__ import annotations

import os
import numpy as np
import torch
import cv2

from rl_policy.model import Actor, load_actor

MAX_DEPTH_M = 30.0
MAX_DEPTH_MM = MAX_DEPTH_M * 1000.0
NUM_LIDAR_RAYS = 3000
TRACED_LIDAR_RAYS = 360
IMG_SIZE = 64

GPU_MEMORY_FRACTION = 0.4


class PolicyRunner:
    """Wraps Actor with preprocessing and LSTM state management."""

    def __init__(self, checkpoint_path: str, device: str = "cuda") -> None:
        self.device = torch.device(device)
        if self.device.type == "cuda" and torch.cuda.is_available():
            torch.cuda.set_per_process_memory_fraction(GPU_MEMORY_FRACTION)
            print(f"GPU memory capped at {GPU_MEMORY_FRACTION:.0%} of available")

        traced_path = checkpoint_path.replace(".pt", "_traced_fp16.pt")
        if os.path.exists(traced_path):
            print(f"Loading TorchScript FP16: {traced_path}")
            self.traced = torch.jit.load(traced_path, map_location=self.device)
            self.traced.eval()
            self.model = load_actor(checkpoint_path, device)
            self.fp16 = True
        else:
            self.traced = None
            self.model = load_actor(checkpoint_path, device)
            self.fp16 = False

        self.hidden: tuple[torch.Tensor, torch.Tensor] | None = None
        self.step_count = 0
        self.reset()

    @property
    def is_rgbd(self) -> bool:
        return self.model.cnn_channels == 4

    @property
    def has_lidar(self) -> bool:
        return self.model.use_lidar

    @property
    def action_dim(self) -> int:
        return self.model.action_dim

    def reset(self) -> None:
        self.hidden = None
        self.step_count = 0

    # ── Image preprocessing ─────────────────────────────────────────

    def preprocess_depth(self, depth_mm: np.ndarray) -> torch.Tensor:
        """(H, W) uint16 mm → (1, 1, 64, 64) normalized."""
        depth_f = depth_mm.astype(np.float32)
        depth_small = cv2.resize(depth_f, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_NEAREST)
        depth_norm = np.clip(depth_small, 0, MAX_DEPTH_MM) / MAX_DEPTH_MM
        return torch.from_numpy(depth_norm[np.newaxis, np.newaxis]).to(self.device)

    def preprocess_rgbd(self, rgb_bgr: np.ndarray, depth_mm: np.ndarray) -> torch.Tensor:
        """(H,W,3) BGR uint8 + (H,W) uint16 mm → (1, 4, 64, 64) normalized RGBD."""
        rgb = cv2.cvtColor(rgb_bgr, cv2.COLOR_BGR2RGB)
        rgb_small = cv2.resize(rgb, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_LINEAR)
        rgb_norm = rgb_small.astype(np.float32) / 255.0

        depth_f = depth_mm.astype(np.float32)
        depth_small = cv2.resize(depth_f, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_NEAREST)
        depth_norm = np.clip(depth_small, 0, MAX_DEPTH_MM) / MAX_DEPTH_MM

        # Stack as (H, W, 4) then transpose to (4, H, W)
        rgbd = np.concatenate([rgb_norm, depth_norm[:, :, np.newaxis]], axis=2)
        rgbd_chw = rgbd.transpose(2, 0, 1)
        return torch.from_numpy(rgbd_chw[np.newaxis]).to(self.device)

    # ── LiDAR preprocessing ─────────────────────────────────────────

    def preprocess_lidar(self, scan_points: list[dict]) -> torch.Tensor:
        """Bin scan points into 3000 fixed angular slots → (1, 3000)."""
        ranges = np.full(NUM_LIDAR_RAYS, MAX_DEPTH_MM, dtype=np.float32)
        for pt in scan_points:
            slot = int(pt["angle"] * NUM_LIDAR_RAYS / 360.0) % NUM_LIDAR_RAYS
            dist = float(pt["dist_mm"])
            if 0 < dist < MAX_DEPTH_MM:
                ranges[slot] = min(ranges[slot], dist)
        ranges /= MAX_DEPTH_MM
        return torch.from_numpy(ranges).unsqueeze(0).to(self.device)

    def _preprocess_lidar_traced(self, scan_points: list[dict]) -> torch.Tensor:
        """Bin scan points into 360 angular slots for traced models."""
        n = TRACED_LIDAR_RAYS
        ranges = np.full(n, MAX_DEPTH_MM, dtype=np.float32)
        for pt in scan_points:
            slot = int(pt["angle"] * n / 360.0) % n
            dist = float(pt["dist_mm"])
            if 0 < dist < MAX_DEPTH_MM:
                ranges[slot] = min(ranges[slot], dist)
        ranges /= MAX_DEPTH_MM
        return torch.from_numpy(ranges).unsqueeze(0).to(self.device)

    # ── State vector computation ─────────────────────────────────────

    def _sample_target_depth(
        self, cx: float, cy: float, w: float, h: float, depth_mm: np.ndarray
    ) -> float:
        fh, fw = depth_mm.shape[:2]
        px, py = int(cx * fw), int(cy * fh)
        r_half = max(1, int(min(w, h) * min(fw, fh) * 0.1))
        roi = depth_mm[
            max(0, py - r_half):min(fh, py + r_half),
            max(0, px - r_half):min(fw, px + r_half),
        ].astype(np.float32)
        valid = roi[roi > 0]
        return float(np.median(valid)) / 1000.0 if len(valid) > 0 else MAX_DEPTH_M

    def compute_state_6d(
        self, cx: float | None, cy: float | None, w: float | None, h: float | None,
        depth_mm: np.ndarray, desired_dist_m: float,
    ) -> torch.Tensor:
        """6D state for depth-only compbottleneck model."""
        desired_dist_norm = desired_dist_m / MAX_DEPTH_M
        if cx is None:
            s = [0.0, 0.0, 1.0, desired_dist_norm, 0.0, 1.0]
        else:
            depth_m = self._sample_target_depth(cx, cy, w, h, depth_mm)
            s = [
                min(w * h, 1.0),
                (cx - 0.5) / 0.5,
                min(depth_m / MAX_DEPTH_M, 1.0),
                desired_dist_norm,
                min(w, 1.0),
                0.0,
            ]
        return torch.tensor([s], dtype=torch.float32, device=self.device)

    def compute_state_4d(
        self, cx: float | None, cy: float | None, w: float | None, h: float | None,
        depth_mm: np.ndarray, desired_dist_m: float,
    ) -> torch.Tensor:
        """4D state for RGBD trace model (bearing, dist, known, desired_dist)."""
        desired_dist_norm = desired_dist_m / MAX_DEPTH_M
        if cx is None:
            s = [0.0, 1.0, 0.0, desired_dist_norm]
        else:
            depth_m = self._sample_target_depth(cx, cy, w, h, depth_mm)
            s = [
                (cx - 0.5) / 0.5,
                min(depth_m / MAX_DEPTH_M, 1.0),
                1.0,
                desired_dist_norm,
            ]
        return torch.tensor([s], dtype=torch.float32, device=self.device)

    def compute_state_3d(
        self, cx: float | None, cy: float | None, w: float | None, h: float | None,
        depth_mm: np.ndarray, desired_dist_m: float,
    ) -> torch.Tensor:
        """3D state for RGBD find model (bearing, dist, known)."""
        if cx is None:
            s = [0.0, 1.0, 0.0]
        else:
            depth_m = self._sample_target_depth(cx, cy, w, h, depth_mm)
            s = [(cx - 0.5) / 0.5, min(depth_m / MAX_DEPTH_M, 1.0), 1.0]
        return torch.tensor([s], dtype=torch.float32, device=self.device)

    def compute_state(self, cx, cy, w, h, depth_mm, desired_dist_m) -> torch.Tensor:
        dim = self.model.state_dim
        if dim == 6:
            return self.compute_state_6d(cx, cy, w, h, depth_mm, desired_dist_m)
        elif dim == 4:
            return self.compute_state_4d(cx, cy, w, h, depth_mm, desired_dist_m)
        elif dim == 3:
            return self.compute_state_3d(cx, cy, w, h, depth_mm, desired_dist_m)
        else:
            raise ValueError(f"Unsupported state_dim={dim}")

    # ── Full inference step ──────────────────────────────────────────

    @torch.inference_mode()
    def step(
        self,
        rgb_bgr: np.ndarray,
        depth_mm: np.ndarray,
        bbox: object | None,
        scan_points: list[dict],
        desired_dist_m: float,
    ) -> list[float]:
        """Run one policy inference step.

        Args:
            rgb_bgr: Camera frame (H, W, 3) BGR uint8.
            depth_mm: Depth frame (H, W) uint16 mm.
            bbox: Object with .cx, .cy, .w, .h (normalized), or None.
            scan_points: LiDAR points from reader.get_scan()["points"].
            desired_dist_m: Target follow distance in meters.

        Returns:
            Action values (roughly [-1, 1]). Length matches action_dim (2 or 3).
        """
        if bbox is not None:
            cx, cy, w, h = bbox.cx, bbox.cy, bbox.w, bbox.h
        else:
            cx = cy = w = h = None

        if self.is_rgbd:
            image = self.preprocess_rgbd(rgb_bgr, depth_mm)
        else:
            image = self.preprocess_depth(depth_mm)

        state = self.compute_state(cx, cy, w, h, depth_mm, desired_dist_m)

        if self.traced is not None:
            lidar = self._preprocess_lidar_traced(scan_points)
            if self.fp16:
                image = image.half()
                state = state.half()
                lidar = lidar.half()

            nl = self.model.lstm.num_layers
            hd = self.model.lstm_hidden_dim
            dtype = torch.float16 if self.fp16 else torch.float32
            if self.hidden is None:
                h0 = torch.zeros(nl, 1, hd, device=self.device, dtype=dtype)
                c0 = torch.zeros(nl, 1, hd, device=self.device, dtype=dtype)
            else:
                h0, c0 = self.hidden
                if self.fp16:
                    h0, c0 = h0.half(), c0.half()

            actions, h_new, c_new = self.traced(image, state, h0, c0, lidar)
            self.hidden = (h_new.detach(), c_new.detach())
        else:
            lidar = self.preprocess_lidar(scan_points) if self.has_lidar else None
            actions, self.hidden = self.model(image, state, self.hidden, lidar)
            self.hidden = (self.hidden[0].detach(), self.hidden[1].detach())

        self.step_count += 1
        return actions[0].float().cpu().tolist()

    @property
    def last_painted_frame(self) -> np.ndarray | None:
        return None
