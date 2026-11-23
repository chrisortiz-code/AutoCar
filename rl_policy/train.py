"""PPO trainer for the RL follow-policy using the 2D sim environment.

Uses the same SimRobot / SimTarget / synthetic sensors as sim_test.py,
wrapped as a Gymnasium environment. Trains the same Actor architecture
from model.py so checkpoints are directly loadable by inference.py.

Requires: pip install gymnasium stable-baselines3

Usage:
    python -m rl_policy.train                          # RGBD + lidar, 3-action
    python -m rl_policy.train --depth-only             # depth-only, 3-action
    python -m rl_policy.train --no-lidar               # RGBD, no lidar
    python -m rl_policy.train --actions 2              # fwd + yaw only
    python -m rl_policy.train --steps 2_000_000        # longer training
    python -m rl_policy.train --resume rl_policy/checkpoints/my.pt
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

import gymnasium as gym
import numpy as np
import torch
import torch.nn as nn
from gymnasium import spaces
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CheckpointCallback, BaseCallback
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor
from stable_baselines3.common.vec_env import SubprocVecEnv, DummyVecEnv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rl_policy.sim_test import (
    SimRobot, SimTarget,
    generate_synthetic_depth, generate_synthetic_rgb,
    compute_bbox, generate_lidar_scan, scale_actions,
    ROOM_HALF_W, ROOM_DEPTH, MAX_RANGE_SPEED, MAX_ROT_SPEED,
    WHEEL_RADIUS_M, WHEELBASE_M,
)
from rl_policy.inference import MAX_DEPTH_MM, IMG_SIZE, NUM_LIDAR_RAYS

import cv2


# ── Environment ─────────────────────────────────────────────────────

class FollowEnv(gym.Env):
    """2D mecanum robot following a target — matches sim_test.py physics."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        use_rgbd: bool = True,
        use_lidar: bool = True,
        action_dim: int = 3,
        state_dim: int = 4,
        episode_steps: int = 500,
        dt: float = 1.0 / 15.0,
    ):
        super().__init__()
        self.use_rgbd = use_rgbd
        self.use_lidar = use_lidar
        self.action_dim = action_dim
        self.state_dim = state_dim
        self.episode_steps = episode_steps
        self.dt = dt

        self.action_space = spaces.Box(-1.0, 1.0, shape=(action_dim,), dtype=np.float32)

        cnn_channels = 4 if use_rgbd else 1
        obs_dict = {
            "image": spaces.Box(0.0, 1.0, shape=(cnn_channels, IMG_SIZE, IMG_SIZE), dtype=np.float32),
            "state": spaces.Box(-1.0, 2.0, shape=(state_dim,), dtype=np.float32),
        }
        if use_lidar:
            obs_dict["lidar"] = spaces.Box(0.0, 1.0, shape=(NUM_LIDAR_RAYS,), dtype=np.float32)

        self.observation_space = spaces.Dict(obs_dict)

        self.robot = SimRobot()
        self.target = SimTarget()
        self.desired_dist = 1.0
        self.step_count = 0
        self.prev_action = np.zeros(action_dim, dtype=np.float32)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        rng = self.np_random

        self.robot = SimRobot(x=0.0, y=0.0, heading=rng.uniform(-math.pi, math.pi))
        dist = rng.uniform(0.5, 4.0)
        angle = rng.uniform(-math.pi, math.pi)
        tx = self.robot.x + dist * math.cos(angle)
        ty = self.robot.y + dist * math.sin(angle)
        tx = np.clip(tx, -ROOM_HALF_W + 0.3, ROOM_HALF_W - 0.3)
        ty = np.clip(ty, -ROOM_HALF_W + 0.3, ROOM_DEPTH - 0.3)
        self.target = SimTarget(x=float(tx), y=float(ty))
        self.desired_dist = float(rng.uniform(0.5, 2.5))
        self.step_count = 0
        self.prev_action = np.zeros(self.action_dim, dtype=np.float32)

        return self._obs(), {}

    def step(self, action: np.ndarray):
        action = np.clip(action, -1.0, 1.0)

        raw_actions = action.tolist()
        vx, vy, ts, rs = scale_actions(raw_actions)
        self.robot.apply_motor_command(vx, vy, ts, rs, self.dt)
        self.step_count += 1

        # Move target slightly (simulates real-world target drift)
        if self.np_random.random() < 0.02:
            self.target.x += self.np_random.uniform(-0.1, 0.1)
            self.target.y += self.np_random.uniform(-0.1, 0.1)

        obs = self._obs()
        reward = self._reward(action)
        self.prev_action = action.copy()

        truncated = self.step_count >= self.episode_steps
        terminated = False

        # Out of room bounds
        if (abs(self.robot.x) > ROOM_HALF_W + 0.5 or
                abs(self.robot.y) > ROOM_DEPTH + 0.5):
            terminated = True
            reward -= 5.0

        return obs, reward, terminated, truncated, {}

    def _obs(self) -> dict:
        depth = generate_synthetic_depth(self.robot, self.target)
        bbox = compute_bbox(self.robot, self.target)

        # Image observation
        if self.use_rgbd:
            rgb = generate_synthetic_rgb(self.robot, self.target)
            rgb_small = cv2.resize(rgb, (IMG_SIZE, IMG_SIZE))
            rgb_norm = rgb_small.astype(np.float32) / 255.0
            depth_small = cv2.resize(depth.astype(np.float32), (IMG_SIZE, IMG_SIZE),
                                     interpolation=cv2.INTER_NEAREST)
            depth_norm = np.clip(depth_small, 0, MAX_DEPTH_MM) / MAX_DEPTH_MM
            image = np.concatenate([rgb_norm, depth_norm[:, :, np.newaxis]], axis=2)
            image = image.transpose(2, 0, 1).astype(np.float32)
        else:
            depth_small = cv2.resize(depth.astype(np.float32), (IMG_SIZE, IMG_SIZE),
                                     interpolation=cv2.INTER_NEAREST)
            depth_norm = np.clip(depth_small, 0, MAX_DEPTH_MM) / MAX_DEPTH_MM
            image = depth_norm[np.newaxis].astype(np.float32)

        # State vector
        if bbox is not None:
            cx, cy, w, h = bbox.cx, bbox.cy, bbox.w, bbox.h
            dist = max(math.sqrt((self.target.x - self.robot.x)**2 +
                                 (self.target.y - self.robot.y)**2), 0.05)
            depth_m = dist
            if self.state_dim == 6:
                state = np.array([
                    min(w * h, 1.0),
                    (cx - 0.5) / 0.5,
                    min(depth_m / 30.0, 1.0),
                    self.desired_dist / 30.0,
                    min(w, 1.0),
                    0.0,
                ], dtype=np.float32)
            elif self.state_dim == 4:
                state = np.array([
                    (cx - 0.5) / 0.5,
                    min(depth_m / 30.0, 1.0),
                    1.0,
                    self.desired_dist / 30.0,
                ], dtype=np.float32)
            else:
                state = np.array([
                    (cx - 0.5) / 0.5,
                    min(depth_m / 30.0, 1.0),
                    1.0,
                ], dtype=np.float32)
        else:
            if self.state_dim == 6:
                state = np.array([0, 0, 1, self.desired_dist / 30.0, 0, 1], dtype=np.float32)
            elif self.state_dim == 4:
                state = np.array([0, 1, 0, self.desired_dist / 30.0], dtype=np.float32)
            else:
                state = np.array([0, 1, 0], dtype=np.float32)

        obs = {"image": image, "state": state}

        if self.use_lidar:
            scan = generate_lidar_scan(self.robot, self.target)
            ranges = np.full(NUM_LIDAR_RAYS, MAX_DEPTH_MM, dtype=np.float32)
            for pt in scan:
                slot = int(pt["angle"] * NUM_LIDAR_RAYS / 360.0) % NUM_LIDAR_RAYS
                d = float(pt["dist_mm"])
                if 0 < d < MAX_DEPTH_MM:
                    ranges[slot] = min(ranges[slot], d)
            ranges /= MAX_DEPTH_MM
            obs["lidar"] = ranges

        return obs

    def _reward(self, action: np.ndarray) -> float:
        dx = self.target.x - self.robot.x
        dy = self.target.y - self.robot.y
        dist = math.sqrt(dx ** 2 + dy ** 2)

        # Distance error
        dist_error = abs(dist - self.desired_dist)
        r_dist = -dist_error * 2.0

        # Bearing alignment: target should be centered in FOV
        angle_to_target = math.atan2(dy, dx)
        bearing_error = abs(angle_to_target - self.robot.heading)
        bearing_error = min(bearing_error, 2 * math.pi - bearing_error)
        r_bearing = -bearing_error * 0.5

        # Bonus for being close to desired distance
        if dist_error < 0.15:
            r_dist += 1.0
        elif dist_error < 0.3:
            r_dist += 0.3

        # Action smoothness penalty
        action_delta = action - self.prev_action
        r_smooth = -0.01 * float(np.sum(action_delta ** 2))

        # Survival bonus
        r_alive = 0.1

        return r_dist + r_bearing + r_smooth + r_alive


# ── Custom feature extractor for SB3 ────────────────────────────────

class ActorFeaturesExtractor(BaseFeaturesExtractor):
    """Extracts CNN + lidar + state features matching the Actor architecture."""

    def __init__(
        self,
        observation_space: spaces.Dict,
        cnn_layer_channels: tuple[int, ...] = (32, 64, 64),
        use_lidar: bool = True,
        lidar_output_dim: int = 64,
    ):
        # Compute feature dim
        cnn_channels = observation_space["image"].shape[0]
        layers = []
        in_ch = cnn_channels
        for i, ch in enumerate(cnn_layer_channels):
            ks = 8 if i == 0 else (4 if i == 1 else 3)
            st = 4 if i == 0 else (2 if i == 1 else 1)
            layers.append(nn.Conv2d(in_ch, ch, kernel_size=ks, stride=st))
            layers.append(nn.ReLU())
            in_ch = ch

        cnn = nn.Sequential(*layers)
        with torch.no_grad():
            dummy = torch.zeros(1, cnn_channels, IMG_SIZE, IMG_SIZE)
            cnn_flat_dim = cnn(dummy).reshape(1, -1).shape[1]

        state_dim = observation_space["state"].shape[0]
        lidar_dim = lidar_output_dim if use_lidar and "lidar" in observation_space.spaces else 0
        features_dim = cnn_flat_dim + state_dim + lidar_dim

        super().__init__(observation_space, features_dim=features_dim)

        self.cnn = cnn
        self.use_lidar = use_lidar and "lidar" in observation_space.spaces

        if self.use_lidar:
            self.lidar_encoder = nn.Sequential(
                nn.Conv1d(1, 32, kernel_size=8, stride=4),
                nn.ReLU(),
                nn.Conv1d(32, 64, kernel_size=4, stride=2),
                nn.ReLU(),
                nn.Conv1d(64, 64, kernel_size=3, stride=1),
                nn.ReLU(),
                nn.AdaptiveAvgPool1d(1),
            )
        else:
            self.lidar_encoder = None

    def forward(self, observations: dict) -> torch.Tensor:
        image = observations["image"]
        state = observations["state"]

        cnn_out = self.cnn(image).reshape(image.shape[0], -1)
        parts = [state, cnn_out]

        if self.lidar_encoder is not None:
            lidar = observations["lidar"].unsqueeze(1)
            lidar_feat = self.lidar_encoder(lidar).squeeze(-1)
            parts.append(lidar_feat)

        return torch.cat(parts, dim=-1)


# ── Checkpoint export callback ──────────────────────────────────────

class ExportCallback(BaseCallback):
    """Periodically export SB3 weights to Actor-compatible checkpoints."""

    def __init__(self, save_dir: str, save_freq: int, env_kwargs: dict, verbose=0):
        super().__init__(verbose)
        self.save_dir = save_dir
        self.save_freq = save_freq
        self.env_kwargs = env_kwargs

    def _on_step(self) -> bool:
        if self.n_calls % self.save_freq == 0:
            path = os.path.join(self.save_dir, f"policy_{self.n_calls}.pt")
            export_sb3_to_actor(self.model, path, self.env_kwargs)
            if self.verbose:
                print(f"Exported Actor checkpoint: {path}")
        return True


def export_sb3_to_actor(sb3_model, path: str, env_kwargs: dict):
    """Convert SB3 policy weights to Actor-loadable checkpoint format."""
    from rl_policy.model import Actor

    policy = sb3_model.policy
    extractor = policy.features_extractor

    cnn_channels = 4 if env_kwargs.get("use_rgbd", True) else 1
    use_lidar = env_kwargs.get("use_lidar", True)
    action_dim = env_kwargs.get("action_dim", 3)
    state_dim = env_kwargs.get("state_dim", 4)

    # Build actor_state_dict with IsaacLab key naming
    actor_sd = {}

    # CNN weights: cnn.{0,2,4}.weight → cnns.policy.{0,2,4}.weight
    for name, param in extractor.cnn.named_parameters():
        actor_sd[f"cnns.policy.{name}"] = param.data.cpu()

    # Lidar encoder
    if use_lidar and extractor.lidar_encoder is not None:
        for name, param in extractor.lidar_encoder.named_parameters():
            actor_sd[f"lidar_encoder.encoder.{name}"] = param.data.cpu()

    # LSTM: SB3 doesn't have one, so init fresh LSTM weights
    # We'll save a full Actor and extract its state dict
    actor = Actor(
        cnn_channels=cnn_channels,
        cnn_layer_channels=(32, 64, 64),
        state_dim=state_dim,
        action_dim=action_dim,
        lstm_hidden_dim=256,
        lstm_num_layers=1,
        mlp_dims=(256,),
        use_lidar=use_lidar,
        lidar_output_dim=64,
    )

    # Copy what we can from SB3
    new_sd = {}
    for name, param in actor.named_parameters():
        if name.startswith("cnn."):
            sb3_name = name.replace("cnn.", "")
            if sb3_name in dict(extractor.cnn.named_parameters()):
                new_sd[name] = dict(extractor.cnn.named_parameters())[sb3_name].data.cpu()
                continue
        if name.startswith("lidar_encoder.") and extractor.lidar_encoder is not None:
            sb3_name = name.replace("lidar_encoder.", "")
            le_params = dict(extractor.lidar_encoder.named_parameters())
            if sb3_name in le_params:
                new_sd[name] = le_params[sb3_name].data.cpu()
                continue
        if name.startswith("mlp."):
            # Map SB3 action_net / value_net to our MLP
            # SB3 mlp_extractor.policy_net.{0,2,...} → mlp.{0,2,...}
            idx = name.split(".")[1]
            param_type = name.split(".")[2]
            sb3_key = f"{idx}.{param_type}"
            mlp_params = dict(policy.mlp_extractor.policy_net.named_parameters())
            if sb3_key in mlp_params:
                new_sd[name] = mlp_params[sb3_key].data.cpu()
                continue
            # Last layer is the action head
            action_params = dict(policy.action_net.named_parameters())
            if param_type in ["weight", "bias"] and name == f"mlp.{len(list(policy.mlp_extractor.policy_net.named_parameters())) // 2 * 2}.{param_type}":
                new_sd[name] = action_params[param_type].data.cpu()
                continue

    # For unmatched params, use actor's randomly initialized weights
    full_sd = actor.state_dict()
    for k in full_sd:
        if k not in new_sd:
            new_sd[k] = full_sd[k]
    actor.load_state_dict(new_sd)

    # Save in IsaacLab format for load_actor() compatibility
    remap_sd = {}
    for name, param in actor.named_parameters():
        new_name = name
        if name.startswith("cnn."):
            new_name = name.replace("cnn.", "cnns.policy.")
        elif name.startswith("lidar_encoder."):
            new_name = name.replace("lidar_encoder.", "lidar_encoder.encoder.")
        elif name.startswith("lidar_proj."):
            new_name = name.replace("lidar_proj.", "lidar_encoder.proj.")
        elif name.startswith("lstm."):
            new_name = name.replace("lstm.", "rnn.rnn.")
        remap_sd[new_name] = param.data.cpu()

    # Add dummy distribution keys (load_actor skips them)
    remap_sd["distribution.std"] = torch.zeros(action_dim)

    torch.save({"actor_state_dict": remap_sd}, path)


# ── Main ────────────────────────────────────────────────────────────

def make_env(env_kwargs: dict, seed: int):
    def _init():
        env = FollowEnv(**env_kwargs)
        env.reset(seed=seed)
        return env
    return _init


def main():
    parser = argparse.ArgumentParser(description="Train RL follow-policy with PPO")
    parser.add_argument("--steps", type=int, default=1_000_000,
                        help="Total training timesteps (default: 1M)")
    parser.add_argument("--depth-only", action="store_true",
                        help="Depth-only CNN (1ch) instead of RGBD (4ch)")
    parser.add_argument("--no-lidar", action="store_true",
                        help="Disable lidar input")
    parser.add_argument("--actions", type=int, default=3, choices=[2, 3],
                        help="Action dimensions: 2=fwd+yaw, 3=fwd+strafe+yaw")
    parser.add_argument("--state-dim", type=int, default=4, choices=[3, 4, 6],
                        help="State vector dimension")
    parser.add_argument("--n-envs", type=int, default=8,
                        help="Parallel environments")
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--n-epochs", type=int, default=10)
    parser.add_argument("--episode-steps", type=int, default=500)
    parser.add_argument("--resume", default="",
                        help="Resume from SB3 zip checkpoint")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--save-freq", type=int, default=50_000,
                        help="Export Actor checkpoint every N steps")
    parser.add_argument("--out-dir", default="rl_policy/checkpoints",
                        help="Output directory for checkpoints")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    use_rgbd = not args.depth_only
    use_lidar = not args.no_lidar

    env_kwargs = dict(
        use_rgbd=use_rgbd,
        use_lidar=use_lidar,
        action_dim=args.actions,
        state_dim=args.state_dim,
        episode_steps=args.episode_steps,
    )

    label = ("rgbd" if use_rgbd else "depth") + f"_{args.actions}a"
    if use_lidar:
        label += "_lidar"
    label += f"_s{args.state_dim}"

    print(f"Training: {label}")
    print(f"  Steps: {args.steps:,}")
    print(f"  Envs: {args.n_envs}")
    print(f"  LR: {args.lr}, Batch: {args.batch_size}, Epochs: {args.n_epochs}")
    print(f"  Motor range: {MAX_RANGE_SPEED} t/s trans, {MAX_ROT_SPEED} t/s rot")
    print()

    envs = SubprocVecEnv([make_env(env_kwargs, seed=i) for i in range(args.n_envs)])

    policy_kwargs = dict(
        features_extractor_class=ActorFeaturesExtractor,
        features_extractor_kwargs=dict(
            cnn_layer_channels=(32, 64, 64),
            use_lidar=use_lidar,
            lidar_output_dim=64,
        ),
        net_arch=dict(pi=[256], vf=[256]),
    )

    if args.resume:
        print(f"Resuming from {args.resume}")
        model = PPO.load(args.resume, env=envs, device=args.device)
    else:
        model = PPO(
            "MultiInputPolicy",
            envs,
            learning_rate=args.lr,
            n_steps=2048,
            batch_size=args.batch_size,
            n_epochs=args.n_epochs,
            gamma=0.99,
            gae_lambda=0.95,
            clip_range=0.2,
            ent_coef=0.01,
            vf_coef=0.5,
            max_grad_norm=0.5,
            policy_kwargs=policy_kwargs,
            verbose=1,
            device=args.device,
        )

    sb3_callback = CheckpointCallback(
        save_freq=args.save_freq,
        save_path=os.path.join(args.out_dir, "sb3"),
        name_prefix=label,
    )
    export_callback = ExportCallback(
        save_dir=args.out_dir,
        save_freq=args.save_freq,
        env_kwargs=env_kwargs,
        verbose=1,
    )

    print("Starting training...")
    t0 = time.time()
    model.learn(total_timesteps=args.steps, callback=[sb3_callback, export_callback])
    elapsed = time.time() - t0
    print(f"\nDone in {elapsed/60:.1f} min ({args.steps/elapsed:.0f} steps/s)")

    # Final export
    final_sb3 = os.path.join(args.out_dir, f"{label}_final.zip")
    model.save(final_sb3)
    final_actor = os.path.join(args.out_dir, f"{label}_final.pt")
    export_sb3_to_actor(model, final_actor, env_kwargs)
    print(f"Saved: {final_sb3}")
    print(f"Saved: {final_actor}")
    print(f"\nTest with: python -m rl_policy.sim_test --checkpoint {final_actor}")


if __name__ == "__main__":
    main()
