"""RL policy inference runner for real-world deployment.

Handles:
- Green overlay painting on CSRT bounding box
- RGBD preprocessing (mean-subtract RGB, normalize depth, resize to 64x64)
- LiDAR binning to 3000 fixed slots
- State vector computation (vis_frac, green_bearing, green_depth, desired_dist_norm)
- LSTM hidden state management
- Action output
"""

from __future__ import annotations

import numpy as np
import torch
import cv2

from rl_policy.model import TraceActor

# Constants matching training config
MAX_DEPTH_M = 30.0
MAX_DEPTH_MM = MAX_DEPTH_M * 1000.0
NUM_LIDAR_RAYS = 3000
IMG_SIZE = 64


GPU_MEMORY_FRACTION = 0.4


class PolicyRunner:
    """Wraps TraceActor with preprocessing and LSTM state management."""

    def __init__(self, checkpoint_path: str, device: str = "cuda") -> None:
        self.device = torch.device(device)
        if self.device.type == "cuda" and torch.cuda.is_available():
            torch.cuda.set_per_process_memory_fraction(GPU_MEMORY_FRACTION)
            print(f"GPU memory capped at {GPU_MEMORY_FRACTION:.0%} of available")
        self.model = TraceActor.load_from_checkpoint(checkpoint_path, device)
        self.hidden: tuple[torch.Tensor, torch.Tensor] | None = None
        self.step_count = 0
        self._last_painted_small: np.ndarray | None = None
        self.reset()

    def reset(self) -> None:
        """Zero LSTM hidden state for a fresh episode."""
        self.hidden = None  # Will be initialized on first forward pass
        self.step_count = 0
        self._last_painted_small = None

    # ── Green overlay ────────────────────────────────────────────────

    @staticmethod
    def paint_green(frame_bgr: np.ndarray, cx: float, cy: float, w: float, h: float) -> np.ndarray:
        """Paint CSRT bounding box region solid green on a copy.

        Args:
            frame_bgr: Original camera frame (H, W, 3) BGR uint8.
            cx, cy: Normalized center of bbox [0, 1].
            w, h: Normalized width/height of bbox [0, 1].

        Returns:
            Copy of frame with bbox region painted solid green.
        """
        painted = frame_bgr.copy()
        fh, fw = painted.shape[:2]
        x1 = max(0, int((cx - w / 2) * fw))
        y1 = max(0, int((cy - h / 2) * fh))
        x2 = min(fw, int((cx + w / 2) * fw))
        y2 = min(fh, int((cy + h / 2) * fh))
        painted[y1:y2, x1:x2] = (0, 255, 0)  # BGR green
        return painted

    # ── RGBD preprocessing ───────────────────────────────────────────

    def preprocess_rgbd(
        self, painted_bgr: np.ndarray, depth_mm: np.ndarray
    ) -> torch.Tensor:
        """Convert painted BGR frame + depth to (1, 4, 64, 64) policy input.

        Matches the sim preprocessing in observations.py:
        - RGB: float [0,1], per-image mean subtraction per channel
        - Depth: clamp to max_depth, normalize to [0,1]
        """
        # Resize to 64x64
        small = cv2.resize(painted_bgr, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_AREA)
        self._last_painted_small = small

        # BGR → RGB, float32 [0, 1]
        rgb = small[:, :, ::-1].astype(np.float32) / 255.0  # (64, 64, 3)

        # HWC → CHW
        rgb_chw = np.transpose(rgb, (2, 0, 1))  # (3, 64, 64)

        # Per-image mean subtraction (matching sim: rgb_chw - rgb_chw.mean(dim=(2,3)))
        for c in range(3):
            rgb_chw[c] -= rgb_chw[c].mean()

        # Depth: resize, clamp, normalize
        depth_f = depth_mm.astype(np.float32)
        depth_small = cv2.resize(depth_f, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_NEAREST)
        depth_norm = np.clip(depth_small, 0, MAX_DEPTH_MM) / MAX_DEPTH_MM  # [0, 1]
        depth_chw = depth_norm[np.newaxis, :, :]  # (1, 64, 64)

        # Stack → (4, 64, 64)
        rgbd = np.concatenate([rgb_chw, depth_chw], axis=0)
        return torch.from_numpy(rgbd).unsqueeze(0).to(self.device)  # (1, 4, 64, 64)

    # ── LiDAR preprocessing ─────────────────────────────────────────

    def preprocess_lidar(self, scan_points: list[dict]) -> torch.Tensor:
        """Bin scan points into 3000 fixed angular slots.

        Args:
            scan_points: List of {"angle": float, "dist_mm": int, "quality": int}.

        Returns:
            (1, 3000) normalized LiDAR tensor.
        """
        ranges = np.full(NUM_LIDAR_RAYS, MAX_DEPTH_MM, dtype=np.float32)
        for pt in scan_points:
            slot = int(pt["angle"] * NUM_LIDAR_RAYS / 360.0) % NUM_LIDAR_RAYS
            dist = float(pt["dist_mm"])
            if 0 < dist < MAX_DEPTH_MM:
                ranges[slot] = min(ranges[slot], dist)  # Keep closest reading per slot

        ranges /= MAX_DEPTH_MM  # Normalize to [0, 1]
        return torch.from_numpy(ranges).unsqueeze(0).to(self.device)  # (1, 3000)

    # ── State vector computation ─────────────────────────────────────

    def compute_state(
        self,
        cx: float | None,
        cy: float | None,
        w: float | None,
        h: float | None,
        depth_mm: np.ndarray,
        desired_dist_m: float,
    ) -> torch.Tensor:
        """Compute the 4-element state vector matching sim observations.

        Uses the green-painted 64x64 frame (self._last_painted_small) to compute
        green mask statistics, exactly matching the sim's compute_green_mask logic.

        Args:
            cx, cy, w, h: Normalized bbox center and size (None if target lost).
            depth_mm: Full-resolution depth frame (H, W) uint16 mm.
            desired_dist_m: Target following distance in meters.

        Returns:
            (1, 4) state tensor: [vis_frac, green_bearing, green_depth, desired_dist_norm].
        """
        desired_dist_norm = desired_dist_m / MAX_DEPTH_M

        if cx is None or self._last_painted_small is None:
            # Target lost: zero visibility
            state = np.array([0.0, 0.0, 1.0, desired_dist_norm], dtype=np.float32)
            return torch.from_numpy(state).unsqueeze(0).to(self.device)

        # Work on the 64x64 painted frame (already has green overlay)
        small = self._last_painted_small  # BGR uint8

        # Convert to float RGB [0, 1] for green mask computation
        rgb = small[:, :, ::-1].astype(np.float32) / 255.0  # (64, 64, 3)
        r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]

        # Green mask matching sim: (g > 0.5) & (g > r + 0.2) & (g > b + 0.2)
        green_mask = (g > 0.5) & (g > r + 0.2) & (g > b + 0.2)
        total_pixels = IMG_SIZE * IMG_SIZE

        green_count = green_mask.sum()
        vis_frac = float(green_count) / total_pixels

        if green_count < 1:
            state = np.array([0.0, 0.0, 1.0, desired_dist_norm], dtype=np.float32)
            return torch.from_numpy(state).unsqueeze(0).to(self.device)

        # Green bearing: weighted column center, normalized to [-1, 1]
        col_indices = np.arange(IMG_SIZE, dtype=np.float32)
        col_sum = green_mask.astype(np.float32).sum(axis=0)  # (64,)
        total_green = col_sum.sum()
        green_center_x = (col_sum * col_indices).sum() / max(total_green, 1.0)
        center_x = IMG_SIZE / 2.0
        green_bearing = (green_center_x - center_x) / center_x  # [-1, 1]

        # Green depth: sample from full-res depth at bbox center
        fh, fw = depth_mm.shape[:2]
        px = int(cx * fw)
        py = int(cy * fh)
        # Sample a small region around center for robustness
        r_half = max(1, int(min(w, h) * min(fw, fh) * 0.1))
        y1d = max(0, py - r_half)
        y2d = min(fh, py + r_half)
        x1d = max(0, px - r_half)
        x2d = min(fw, px + r_half)
        depth_roi = depth_mm[y1d:y2d, x1d:x2d].astype(np.float32)
        valid = depth_roi[depth_roi > 0]
        if len(valid) > 0:
            depth_m = float(np.median(valid)) / 1000.0
        else:
            depth_m = MAX_DEPTH_M
        green_depth = min(depth_m / MAX_DEPTH_M, 1.0)

        state = np.array([vis_frac, green_bearing, green_depth, desired_dist_norm], dtype=np.float32)
        return torch.from_numpy(state).unsqueeze(0).to(self.device)

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
            bbox: Object with .cx, .cy, .w, .h (normalized), or None if lost.
            scan_points: LiDAR points from reader.get_scan()["points"].
            desired_dist_m: Target follow distance in meters.

        Returns:
            [forward, strafe, yaw] raw action values (roughly [-1, 1]).
        """
        if bbox is not None:
            cx, cy, w, h = bbox.cx, bbox.cy, bbox.w, bbox.h
            painted = self.paint_green(rgb_bgr, cx, cy, w, h)
        else:
            cx = cy = w = h = None
            painted = rgb_bgr

        rgbd = self.preprocess_rgbd(painted, depth_mm)
        lidar = self.preprocess_lidar(scan_points)
        state = self.compute_state(cx, cy, w, h, depth_mm, desired_dist_m)

        actions, self.hidden = self.model(rgbd, lidar, state, self.hidden)
        # Detach hidden state to prevent memory leak
        self.hidden = (self.hidden[0].detach(), self.hidden[1].detach())

        self.step_count += 1
        return actions[0].cpu().tolist()  # [forward, strafe, yaw]

    @property
    def last_painted_frame(self) -> np.ndarray | None:
        """Return the last 64x64 painted frame for debug visualization."""
        return self._last_painted_small
