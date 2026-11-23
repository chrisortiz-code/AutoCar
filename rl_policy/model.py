"""Standalone actor networks for Jetson deployment — pure PyTorch, no rsl_rl.

Auto-detects architecture from checkpoint weights. Supports all CNN+LSTM
variants trained in IsaacLab (depth-only, RGBD, with/without lidar,
any hidden dim / layer count / MLP depth).
"""

from __future__ import annotations

import torch
import torch.nn as nn


class Actor(nn.Module):
    """Configurable CNN+LSTM actor built to match a checkpoint's architecture."""

    def __init__(
        self,
        cnn_channels: int,
        cnn_layer_channels: tuple[int, ...],
        state_dim: int,
        action_dim: int,
        lstm_hidden_dim: int = 256,
        lstm_num_layers: int = 1,
        mlp_dims: tuple[int, ...] = (256,),
        use_compressor: bool = False,
        cnn_compress_dim: int = 128,
        use_lidar: bool = True,
        lidar_output_dim: int = 64,
        lidar_use_proj: bool = False,
    ) -> None:
        super().__init__()
        self.use_compressor = use_compressor
        self.use_lidar = use_lidar
        self.lidar_use_proj = lidar_use_proj
        self.cnn_channels = cnn_channels
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.lstm_hidden_dim = lstm_hidden_dim

        layers: list[nn.Module] = []
        in_ch = cnn_channels
        for ch in cnn_layer_channels:
            ks = 8 if in_ch == cnn_channels else (4 if len(layers) == 2 else 3)
            st = 4 if in_ch == cnn_channels else (2 if len(layers) == 2 else 1)
            layers.append(nn.Conv2d(in_ch, ch, kernel_size=ks, stride=st))
            layers.append(nn.ReLU())
            in_ch = ch
        self.cnn = nn.Sequential(*layers)

        # Compute flat dim: run a dummy through CNN
        with torch.no_grad():
            dummy = torch.zeros(1, cnn_channels, 64, 64)
            cnn_flat_dim = self.cnn(dummy).reshape(1, -1).shape[1]

        if use_compressor:
            self.rgbd_compressor = nn.Sequential(
                nn.Linear(cnn_flat_dim, cnn_compress_dim),
                nn.ReLU(),
            )
            vision_dim = cnn_compress_dim
        else:
            self.rgbd_compressor = None
            vision_dim = cnn_flat_dim

        if use_lidar:
            self.lidar_encoder = nn.Sequential(
                nn.Conv1d(1, 32, kernel_size=8, stride=4),
                nn.ReLU(),
                nn.Conv1d(32, 64, kernel_size=4, stride=2),
                nn.ReLU(),
                nn.Conv1d(64, 64, kernel_size=3, stride=1),
                nn.ReLU(),
                nn.AdaptiveAvgPool1d(1),
            )
            if lidar_use_proj:
                self.lidar_proj = nn.Linear(64, lidar_output_dim)
            else:
                self.lidar_proj = None
                lidar_output_dim = 64
        else:
            self.lidar_encoder = None
            self.lidar_proj = None
            lidar_output_dim = 0

        lstm_input = vision_dim + lidar_output_dim + state_dim
        self.lstm = nn.LSTM(
            input_size=lstm_input, hidden_size=lstm_hidden_dim,
            num_layers=lstm_num_layers, batch_first=False,
        )

        mlp_layers: list[nn.Module] = []
        prev = lstm_hidden_dim
        for dim in mlp_dims:
            mlp_layers.append(nn.Linear(prev, dim))
            mlp_layers.append(nn.ELU())
            prev = dim
        mlp_layers.append(nn.Linear(prev, action_dim))
        self.mlp = nn.Sequential(*mlp_layers)

    def forward(
        self,
        image: torch.Tensor,
        state: torch.Tensor,
        hidden: tuple[torch.Tensor, torch.Tensor] | None = None,
        lidar: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, tuple[torch.Tensor, torch.Tensor]]:
        batch_size = image.shape[0]

        cnn_out = self.cnn(image)
        cnn_flat = cnn_out.reshape(batch_size, -1)
        vision_feat = self.rgbd_compressor(cnn_flat) if self.rgbd_compressor else cnn_flat

        parts = [state, vision_feat]
        if self.lidar_encoder is not None and lidar is not None:
            lidar_feat = self.lidar_encoder(lidar.unsqueeze(1)).squeeze(-1)
            if self.lidar_proj is not None:
                lidar_feat = self.lidar_proj(lidar_feat)
            parts.append(lidar_feat)

        combined = torch.cat(parts, dim=-1)
        lstm_in = combined.unsqueeze(0)
        if hidden is None:
            hidden = self._init_hidden(batch_size, image.device)
        lstm_out, hidden_new = self.lstm(lstm_in, hidden)

        actions = self.mlp(lstm_out.squeeze(0))
        return actions, hidden_new

    def _init_hidden(
        self, batch_size: int, device: torch.device
    ) -> tuple[torch.Tensor, torch.Tensor]:
        nl = self.lstm.num_layers
        hd = self.lstm_hidden_dim
        h0 = torch.zeros(nl, batch_size, hd, device=device)
        c0 = torch.zeros(nl, batch_size, hd, device=device)
        return (h0, c0)


def _remap_keys(actor_sd: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    key_map = {}
    for k, v in actor_sd.items():
        new_k = k
        if k.startswith("cnns.policy."):
            new_k = k.replace("cnns.policy.", "cnn.")
        elif k.startswith("rgbd_compressor.compress."):
            new_k = k.replace("rgbd_compressor.compress.", "rgbd_compressor.")
        elif k.startswith("lidar_encoder.encoder."):
            new_k = k.replace("lidar_encoder.encoder.", "lidar_encoder.")
        elif k.startswith("lidar_encoder.proj."):
            new_k = k.replace("lidar_encoder.proj.", "lidar_proj.")
        elif k.startswith("rnn.rnn."):
            new_k = k.replace("rnn.rnn.", "lstm.")
        elif k.startswith("distribution."):
            continue
        key_map[new_k] = v
    return key_map


def _detect_variant(actor_sd: dict[str, torch.Tensor]) -> dict:
    """Inspect checkpoint weights to determine full model configuration."""
    # CNN architecture
    cnn_channels = actor_sd["cnns.policy.0.weight"].shape[1]
    cnn_layer_channels = []
    for i in range(0, 20, 2):
        key = f"cnns.policy.{i}.weight"
        if key not in actor_sd:
            break
        cnn_layer_channels.append(actor_sd[key].shape[0])

    # Compressor
    has_compressor = "rgbd_compressor.compress.0.weight" in actor_sd
    cnn_compress_dim = 128
    if has_compressor:
        cnn_compress_dim = actor_sd["rgbd_compressor.compress.0.bias"].shape[0]
        vision_dim = cnn_compress_dim
    else:
        # Compute CNN flat dim from last conv: run a dummy
        vision_dim = actor_sd["rnn.rnn.weight_ih_l0"].shape[1]  # will subtract lidar+state below

    # LiDAR
    has_lidar = any(k.startswith("lidar_encoder.") for k in actor_sd)
    has_lidar_proj = "lidar_encoder.proj.weight" in actor_sd
    lidar_output_dim = 64
    if has_lidar and has_lidar_proj:
        lidar_output_dim = actor_sd["lidar_encoder.proj.bias"].shape[0]
    elif not has_lidar:
        lidar_output_dim = 0

    # LSTM
    lstm_hidden_dim = actor_sd["rnn.rnn.weight_hh_l0"].shape[1]
    lstm_num_layers = 0
    while f"rnn.rnn.weight_ih_l{lstm_num_layers}" in actor_sd:
        lstm_num_layers += 1

    # MLP layers (pairs: weight+bias at indices 0, 2, 4, ...)
    mlp_dims = []
    for i in range(0, 20, 2):
        key = f"mlp.{i}.weight"
        if key not in actor_sd:
            break
        out_dim = actor_sd[key].shape[0]
        in_dim = actor_sd[key].shape[1]
        # Last linear is the action head, intermediate are hidden
        next_key = f"mlp.{i + 2}.weight"
        if next_key in actor_sd:
            mlp_dims.append(out_dim)

    action_dim = actor_sd[f"mlp.{len(mlp_dims) * 2}.bias"].shape[0]

    # State dim
    lstm_input_dim = actor_sd["rnn.rnn.weight_ih_l0"].shape[1]
    if has_compressor:
        state_dim = lstm_input_dim - cnn_compress_dim - (lidar_output_dim if has_lidar else 0)
    else:
        state_dim = lstm_input_dim - (lidar_output_dim if has_lidar else 0)
        # Need CNN flat dim — infer from compressor input or LSTM input
        # vision_dim = lstm_input_dim - lidar_output_dim - state_dim ... circular
        # Instead, compute from CNN layer shapes: last_channels * 4 * 4
        cnn_flat = cnn_layer_channels[-1] * 4 * 4
        state_dim = lstm_input_dim - cnn_flat - (lidar_output_dim if has_lidar else 0)

    return dict(
        cnn_channels=cnn_channels,
        cnn_layer_channels=tuple(cnn_layer_channels),
        state_dim=state_dim,
        action_dim=action_dim,
        lstm_hidden_dim=lstm_hidden_dim,
        lstm_num_layers=lstm_num_layers,
        mlp_dims=tuple(mlp_dims),
        use_compressor=has_compressor,
        cnn_compress_dim=cnn_compress_dim,
        use_lidar=has_lidar,
        lidar_output_dim=lidar_output_dim,
        lidar_use_proj=has_lidar_proj,
    )


def load_actor(path: str, device: str = "cuda") -> Actor:
    """Auto-detect variant and load actor from an rsl_rl PPO checkpoint."""
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    actor_sd = ckpt["actor_state_dict"]

    cfg = _detect_variant(actor_sd)
    label = "depth" if cfg["cnn_channels"] == 1 else "rgbd"
    print(f"Loaded: {label} | cnn={list(cfg['cnn_layer_channels'])} "
          f"state={cfg['state_dim']} actions={cfg['action_dim']} "
          f"lstm={cfg['lstm_hidden_dim']}x{cfg['lstm_num_layers']} "
          f"lidar={cfg['use_lidar']}")

    model = Actor(**cfg)
    model.load_state_dict(_remap_keys(actor_sd), strict=True)
    model.to(device)
    model.eval()
    return model


TraceActor = Actor
