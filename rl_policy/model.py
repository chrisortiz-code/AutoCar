"""Standalone actor network matching the CNNRNNModel checkpoint from IsaacLab training.

Architecture reconstructed from model_best.pt state_dict keys:
    cnns.policy.{0,2,4}              — 3-layer Conv2d (RGBD encoder)
    rgbd_compressor.compress.0       — Linear(1024→128) + ReLU
    lidar_encoder.encoder.{0,2,4,6}  — 3-layer Conv1d + AdaptiveAvgPool1d
    rnn.rnn                          — LSTM(196, 256, 1 layer)
    mlp.{0,2}                        — Linear(256→256)+ELU, Linear(256→3)

No rsl_rl or IsaacLab dependency — pure PyTorch.
"""

from __future__ import annotations

import torch
import torch.nn as nn


class TraceActor(nn.Module):
    """Full actor: RGBD(128) + LiDAR(64) + State(4) = 196 → LSTM(256) → MLP → 3 actions."""

    def __init__(self) -> None:
        super().__init__()

        # CNN encoder for RGBD (4, 64, 64) → flatten 1024
        # Keys: cnns.policy.{0,2,4}.weight/bias
        self.cnn = nn.Sequential(
            nn.Conv2d(4, 32, kernel_size=8, stride=4),   # 0
            nn.ReLU(),                                    # 1
            nn.Conv2d(32, 64, kernel_size=4, stride=2),   # 2
            nn.ReLU(),                                    # 3
            nn.Conv2d(64, 64, kernel_size=3, stride=1),   # 4
            nn.ReLU(),                                    # 5
        )

        # Bottleneck compressor: 1024 → 128
        # Keys: rgbd_compressor.compress.0.weight/bias
        self.rgbd_compressor = nn.Sequential(
            nn.Linear(1024, 128),
            nn.ReLU(),
        )

        # LiDAR encoder: (1, 3000) → 64
        # Keys: lidar_encoder.encoder.{0,2,4}.weight/bias + pool at index 6
        self.lidar_encoder = nn.Sequential(
            nn.Conv1d(1, 32, kernel_size=8, stride=4),    # 0
            nn.ReLU(),                                    # 1
            nn.Conv1d(32, 64, kernel_size=4, stride=2),   # 2
            nn.ReLU(),                                    # 3
            nn.Conv1d(64, 64, kernel_size=3, stride=1),   # 4
            nn.ReLU(),                                    # 5
            nn.AdaptiveAvgPool1d(1),                      # 6
        )

        # LSTM: input_size=196 (128+64+4), hidden=256
        # Keys: rnn.rnn.weight_ih_l0, rnn.rnn.weight_hh_l0, rnn.rnn.bias_ih_l0, rnn.rnn.bias_hh_l0
        self.lstm = nn.LSTM(input_size=196, hidden_size=256, num_layers=1, batch_first=False)

        # MLP head: 256 → 256 → 3
        # Keys: mlp.0.weight/bias, mlp.2.weight/bias
        self.mlp = nn.Sequential(
            nn.Linear(256, 256),
            nn.ELU(),
            nn.Linear(256, 3),
        )

    def forward(
        self,
        rgbd: torch.Tensor,
        lidar: torch.Tensor,
        state: torch.Tensor,
        hidden: tuple[torch.Tensor, torch.Tensor] | None = None,
    ) -> tuple[torch.Tensor, tuple[torch.Tensor, torch.Tensor]]:
        """Forward pass.

        Args:
            rgbd: (B, 4, 64, 64) mean-subtracted RGB + normalized depth.
            lidar: (B, 3000) normalized LiDAR ranges.
            state: (B, 4) [vis_frac, green_bearing, green_depth, desired_dist_norm].
            hidden: LSTM hidden state (h, c), each (1, B, 256). None to init zeros.

        Returns:
            actions: (B, 3) [forward, strafe, yaw].
            hidden: Updated LSTM hidden state.
        """
        batch_size = rgbd.shape[0]

        # CNN → flatten → compress
        cnn_out = self.cnn(rgbd)                               # (B, 64, 4, 4)
        cnn_flat = cnn_out.reshape(batch_size, -1)             # (B, 1024)
        rgbd_feat = self.rgbd_compressor(cnn_flat)             # (B, 128)

        # LiDAR encode
        lidar_in = lidar.unsqueeze(1)                          # (B, 1, 3000)
        lidar_feat = self.lidar_encoder(lidar_in).squeeze(-1)  # (B, 64)

        # Concatenate all features
        combined = torch.cat([state, rgbd_feat, lidar_feat], dim=-1)  # (B, 196)

        # LSTM expects (seq_len, batch, features) — single step
        lstm_in = combined.unsqueeze(0)                        # (1, B, 196)
        if hidden is None:
            hidden = self._init_hidden(batch_size, rgbd.device)
        lstm_out, hidden_new = self.lstm(lstm_in, hidden)      # (1, B, 256)

        # MLP head
        actions = self.mlp(lstm_out.squeeze(0))                # (B, 3)
        return actions, hidden_new

    def _init_hidden(
        self, batch_size: int, device: torch.device
    ) -> tuple[torch.Tensor, torch.Tensor]:
        h0 = torch.zeros(1, batch_size, 256, device=device)
        c0 = torch.zeros(1, batch_size, 256, device=device)
        return (h0, c0)

    @staticmethod
    def load_from_checkpoint(path: str, device: str = "cuda") -> "TraceActor":
        """Load actor weights from an rsl_rl PPO checkpoint.

        The checkpoint stores weights under 'actor_state_dict' with keys like:
            cnns.policy.0.weight → self.cnn.0.weight
            rgbd_compressor.compress.0.weight → self.rgbd_compressor.0.weight
            lidar_encoder.encoder.* → self.lidar_encoder.*
            rnn.rnn.* → self.lstm.*
            mlp.* → self.mlp.*
        """
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        actor_sd = ckpt["actor_state_dict"]

        # Remap keys from training module structure to our flat structure
        key_map = {}
        for k in actor_sd:
            new_k = k
            # cnns.policy.X → cnn.X
            if k.startswith("cnns.policy."):
                new_k = k.replace("cnns.policy.", "cnn.")
            # rgbd_compressor.compress.0.* → rgbd_compressor.0.*
            elif k.startswith("rgbd_compressor.compress."):
                new_k = k.replace("rgbd_compressor.compress.", "rgbd_compressor.")
            # lidar_encoder.encoder.* → lidar_encoder.*
            elif k.startswith("lidar_encoder.encoder."):
                new_k = k.replace("lidar_encoder.encoder.", "lidar_encoder.")
            # rnn.rnn.* → lstm.*
            elif k.startswith("rnn.rnn."):
                new_k = k.replace("rnn.rnn.", "lstm.")
            # mlp.* → mlp.* (unchanged)
            # distribution.std_param → skip (not used for inference)
            elif k.startswith("distribution."):
                continue
            key_map[new_k] = actor_sd[k]

        model = TraceActor()
        model.load_state_dict(key_map, strict=True)
        model.to(device)
        model.eval()
        return model
