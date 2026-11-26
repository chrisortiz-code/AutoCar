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

from rl_policy.model import Actor, load_actor, _detect_variant, _remap_keys

DEFAULT_DEPTH_RANGE_M = 30.0  # Existing AutoCar models use this range.
TRACE_DEPTH_RANGE_M = 10.0  # Trace depthonly_curriculum checkpoint training range.
MAX_DEPTH_M = DEFAULT_DEPTH_RANGE_M  # Backward-compatible default for callers.
MAX_DEPTH_MM = MAX_DEPTH_M * 1000.0
MAX_LIDAR_RANGE_M = 30.0
MAX_LIDAR_RANGE_MM = MAX_LIDAR_RANGE_M * 1000.0
NUM_LIDAR_RAYS = 3000
TRACED_LIDAR_RAYS = 360
IMG_SIZE = 64

GPU_MEMORY_FRACTION = 0.4


class PolicyRunner:
    """Wraps Actor with preprocessing and LSTM state management."""

    def __init__(self, checkpoint_path: str, device: str = "cuda") -> None:
        self.device = torch.device(device)
        checkpoint_name = os.path.basename(checkpoint_path).lower()
        self.depth_range_m = (
            TRACE_DEPTH_RANGE_M
            if "depthonly_curriculum" in checkpoint_name
            else DEFAULT_DEPTH_RANGE_M
        )
        self.depth_range_mm = self.depth_range_m * 1000.0
        print(f"Input depth/state range: {self.depth_range_m:g} m; LiDAR range: {MAX_LIDAR_RANGE_M:g} m")
        if self.device.type == "cuda" and torch.cuda.is_available():
            torch.cuda.set_per_process_memory_fraction(GPU_MEMORY_FRACTION)
            print(f"GPU memory capped at {GPU_MEMORY_FRACTION:.0%} of available")

        traced_path = checkpoint_path.replace(".pt", "_traced_fp16.pt")
        has_raw = os.path.exists(checkpoint_path)
        has_traced = os.path.exists(traced_path)

        if has_traced:
            print(f"Loading TorchScript FP16: {traced_path}")
            self.traced = torch.jit.load(traced_path, map_location=self.device)
            self.traced.eval()
            self.fp16 = True
            if has_raw:
                self.model = load_actor(checkpoint_path, device)
            else:
                self.model = self._build_model_from_traced(traced_path, device)
        elif has_raw:
            self.traced = None
            self.model = load_actor(checkpoint_path, device)
            self.fp16 = False
        else:
            raise FileNotFoundError(
                f"Neither {checkpoint_path} nor {traced_path} found"
            )

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

    @staticmethod
    def _build_model_from_traced(traced_path: str, device: str) -> Actor:
        """Build an Actor stub from the traced model's saved raw checkpoint metadata.

        When only the traced FP16 file exists, we load the original actor
        state dict that was embedded during export (the traced file is a
        TorchScript archive — we pull the original config from its weights).
        Alternatively, we infer the config from the traced parameter shapes.
        """
        traced = torch.jit.load(traced_path, map_location="cpu")
        sd = dict(traced.state_dict())
        remap = {}
        for k, v in sd.items():
            clean = k.replace("actor.", "", 1) if k.startswith("actor.") else k
            remap[clean] = v
        cnn_ch = remap["cnn.0.weight"].shape[1]
        cnn_layers = []
        for i in range(0, 20, 2):
            key = f"cnn.{i}.weight"
            if key not in remap:
                break
            cnn_layers.append(remap[key].shape[0])
        has_lidar = any(k.startswith("lidar_encoder.") for k in remap)
        has_lidar_proj = "lidar_proj.weight" in remap
        lidar_out = 64
        if has_lidar and has_lidar_proj:
            lidar_out = remap["lidar_proj.bias"].shape[0]
        elif not has_lidar:
            lidar_out = 0
        has_comp = "rgbd_compressor.0.weight" in remap
        comp_dim = 128
        if has_comp:
            comp_dim = remap["rgbd_compressor.0.bias"].shape[0]
        hd = remap["lstm.weight_hh_l0"].shape[1]
        nl = 0
        while f"lstm.weight_ih_l{nl}" in remap:
            nl += 1
        mlp_dims = []
        for i in range(0, 20, 2):
            key = f"mlp.{i}.weight"
            if key not in remap:
                break
            if f"mlp.{i + 2}.weight" in remap:
                mlp_dims.append(remap[key].shape[0])
        action_dim = remap[f"mlp.{len(mlp_dims) * 2}.bias"].shape[0]
        lstm_input = remap["lstm.weight_ih_l0"].shape[1]
        vision_dim = comp_dim if has_comp else cnn_layers[-1] * 4 * 4
        state_dim = lstm_input - vision_dim - (lidar_out if has_lidar else 0)

        cfg = dict(
            cnn_channels=cnn_ch, cnn_layer_channels=tuple(cnn_layers),
            state_dim=state_dim, action_dim=action_dim,
            lstm_hidden_dim=hd, lstm_num_layers=nl,
            mlp_dims=tuple(mlp_dims), use_compressor=has_comp,
            cnn_compress_dim=comp_dim, use_lidar=has_lidar,
            lidar_output_dim=lidar_out, lidar_use_proj=has_lidar_proj,
        )
        label = "depth" if cnn_ch == 1 else "rgbd"
        print(f"Loaded (traced-only): {label} | cnn={list(cnn_layers)} "
              f"state={state_dim} actions={action_dim} "
              f"lstm={hd}x{nl} lidar={has_lidar}")
        model = Actor(**cfg)
        model.load_state_dict(remap, strict=True)
        model.to(device).eval()
        return model

    def reset(self) -> None:
        self.hidden = None
        self.step_count = 0

    # ── Image preprocessing ─────────────────────────────────────────

    def preprocess_depth(self, depth_mm: np.ndarray) -> torch.Tensor:
        """(H, W) uint16 mm → (1, 1, 64, 64) normalized."""
        depth_f = depth_mm.astype(np.float32)
        depth_small = cv2.resize(depth_f, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_NEAREST)
        depth_norm = np.clip(depth_small, 0, self.depth_range_mm) / self.depth_range_mm
        return torch.from_numpy(depth_norm[np.newaxis, np.newaxis]).to(self.device)

    def preprocess_rgbd(self, rgb_bgr: np.ndarray, depth_mm: np.ndarray) -> torch.Tensor:
        """(H,W,3) BGR uint8 + (H,W) uint16 mm → (1, 4, 64, 64) normalized RGBD."""
        rgb = cv2.cvtColor(rgb_bgr, cv2.COLOR_BGR2RGB)
        rgb_small = cv2.resize(rgb, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_LINEAR)
        rgb_norm = rgb_small.astype(np.float32) / 255.0

        depth_f = depth_mm.astype(np.float32)
        depth_small = cv2.resize(depth_f, (IMG_SIZE, IMG_SIZE), interpolation=cv2.INTER_NEAREST)
        depth_norm = np.clip(depth_small, 0, self.depth_range_mm) / self.depth_range_mm

        # Stack as (H, W, 4) then transpose to (4, H, W)
        rgbd = np.concatenate([rgb_norm, depth_norm[:, :, np.newaxis]], axis=2)
        rgbd_chw = rgbd.transpose(2, 0, 1)
        return torch.from_numpy(rgbd_chw[np.newaxis]).to(self.device)

    # ── LiDAR preprocessing ─────────────────────────────────────────

    def preprocess_lidar(self, scan_points: list[dict]) -> torch.Tensor:
        """Bin scan points into 3000 fixed angular slots → (1, 3000).

        Isaac Sim trains with horizontal_fov_range=(-180, 180), so index 0
        corresponds to -180° (behind the robot) and index N/2 to 0° (forward).
        Real RPLIDAR reports 0°–360° with 0° forward, so we shift by +180°.
        """
        ranges = np.full(NUM_LIDAR_RAYS, MAX_LIDAR_RANGE_MM, dtype=np.float32)
        for pt in scan_points:
            angle = (pt["angle"] + 180.0) % 360.0
            slot = int(angle * NUM_LIDAR_RAYS / 360.0) % NUM_LIDAR_RAYS
            dist = float(pt["dist_mm"])
            if 0 < dist < MAX_LIDAR_RANGE_MM:
                ranges[slot] = min(ranges[slot], dist)
        ranges /= MAX_LIDAR_RANGE_MM
        return torch.from_numpy(ranges).unsqueeze(0).to(self.device)

    def _preprocess_lidar_traced(self, scan_points: list[dict]) -> torch.Tensor:
        """Bin scan points into 360 angular slots for traced models."""
        n = TRACED_LIDAR_RAYS
        ranges = np.full(n, MAX_LIDAR_RANGE_MM, dtype=np.float32)
        for pt in scan_points:
            angle = (pt["angle"] + 180.0) % 360.0
            slot = int(angle * n / 360.0) % n
            dist = float(pt["dist_mm"])
            if 0 < dist < MAX_LIDAR_RANGE_MM:
                ranges[slot] = min(ranges[slot], dist)
        ranges /= MAX_LIDAR_RANGE_MM
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
        return float(np.median(valid)) / 1000.0 if len(valid) > 0 else self.depth_range_m

    def compute_state_6d(
        self, cx: float | None, cy: float | None, w: float | None, h: float | None,
        depth_mm: np.ndarray, desired_dist_m: float,
    ) -> torch.Tensor:
        """6D state for depth-only compbottleneck model."""
        desired_dist_norm = desired_dist_m / self.depth_range_m
        if cx is None:
            s = [0.0, 0.0, 1.0, desired_dist_norm, 0.0, 1.0]
        else:
            depth_m = self._sample_target_depth(cx, cy, w, h, depth_mm)
            s = [
                min(w * h, 1.0),
                (cx - 0.5) / 0.5,
                min(depth_m / self.depth_range_m, 1.0),
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
        desired_dist_norm = desired_dist_m / self.depth_range_m
        if cx is None:
            s = [0.0, 1.0, 0.0, desired_dist_norm]
        else:
            depth_m = self._sample_target_depth(cx, cy, w, h, depth_mm)
            s = [
                (cx - 0.5) / 0.5,
                min(depth_m / self.depth_range_m, 1.0),
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
            s = [(cx - 0.5) / 0.5, min(depth_m / self.depth_range_m, 1.0), 1.0]
        return torch.tensor([s], dtype=torch.float32, device=self.device)

    def compute_lidar_sectors(
        self, scan_points: list[dict], num_sectors: int = 8,
    ) -> list[float]:
        """Compute min normalized distance per angular sector from LiDAR scan."""
        sectors = [1.0] * num_sectors
        for pt in scan_points:
            angle = (pt["angle"] + 180.0) % 360.0
            sector_idx = int(angle * num_sectors / 360.0) % num_sectors
            dist = float(pt["dist_mm"])
            if 0 < dist < MAX_LIDAR_RANGE_MM:
                norm_dist = dist / MAX_LIDAR_RANGE_MM
                sectors[sector_idx] = min(sectors[sector_idx], norm_dist)
        return sectors

    def compute_state_sectored(
        self, cx: float | None, cy: float | None, w: float | None, h: float | None,
        depth_mm: np.ndarray, desired_dist_m: float,
        scan_points: list[dict] | None = None,
        num_sectors: int = 12,
    ) -> torch.Tensor:
        """6D base + N LiDAR sector min distances."""
        base = self.compute_state_6d(cx, cy, w, h, depth_mm, desired_dist_m)
        sectors = self.compute_lidar_sectors(scan_points or [], num_sectors)
        sector_t = torch.tensor([sectors], dtype=torch.float32, device=self.device)
        return torch.cat([base, sector_t], dim=-1)

    def compute_state(self, cx, cy, w, h, depth_mm, desired_dist_m,
                      scan_points=None) -> torch.Tensor:
        dim = self.model.state_dim
        if dim > 6 and (dim - 6) in (8, 12, 16, 24, 32):
            return self.compute_state_sectored(
                cx, cy, w, h, depth_mm, desired_dist_m, scan_points, num_sectors=dim - 6,
            )
        elif dim == 6:
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

        state = self.compute_state(cx, cy, w, h, depth_mm, desired_dist_m, scan_points)

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
