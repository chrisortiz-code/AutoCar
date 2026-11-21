"""Export actor-only checkpoints to TorchScript FP16 for Jetson deployment.

Usage:
    python rl_policy/export_jetson.py                    # all checkpoints
    python rl_policy/export_jetson.py rgbd_trace.pt      # single file
"""

from __future__ import annotations

import os
import sys
import time

import torch
import torch.nn as nn

from model import Actor, load_actor

CHECKPOINTS_DIR = os.path.join(os.path.dirname(__file__), "checkpoints")
LIDAR_RAYS = 360


class TracableActor(nn.Module):
    """Wrapper that makes Actor traceable by removing Optional args."""

    def __init__(self, actor: Actor) -> None:
        super().__init__()
        self.actor = actor

    def forward(
        self,
        image: torch.Tensor,
        state: torch.Tensor,
        h0: torch.Tensor,
        c0: torch.Tensor,
        lidar: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        actions, (h_new, c_new) = self.actor(
            image, state, hidden=(h0, c0),
            lidar=lidar if self.actor.use_lidar else None,
        )
        return actions, h_new, c_new


def export_one(checkpoint_path: str, device: str = "cpu") -> str:
    name = os.path.splitext(os.path.basename(checkpoint_path))[0]
    out_path = os.path.join(CHECKPOINTS_DIR, f"{name}_traced_fp16.pt")

    print(f"\n{'=' * 60}")
    print(f"Exporting: {name}")
    print(f"{'=' * 60}")

    actor = load_actor(checkpoint_path, device=device)
    actor.eval()

    wrapper = TracableActor(actor).to(device).eval()

    batch = 1
    nl = actor.lstm.num_layers
    hd = actor.lstm_hidden_dim

    dummy_image = torch.zeros(batch, actor.cnn_channels, 64, 64, device=device)
    dummy_state = torch.zeros(batch, actor.state_dim, device=device)
    dummy_h0 = torch.zeros(nl, batch, hd, device=device)
    dummy_c0 = torch.zeros(nl, batch, hd, device=device)
    dummy_lidar = torch.zeros(batch, LIDAR_RAYS, device=device)

    # Verify forward pass works
    with torch.no_grad():
        actions, h_new, c_new = wrapper(
            dummy_image, dummy_state, dummy_h0, dummy_c0, dummy_lidar,
        )
    print(f"  Forward OK — actions shape: {actions.shape}")

    # Convert to FP16
    wrapper.half()
    dummy_image = dummy_image.half()
    dummy_state = dummy_state.half()
    dummy_h0 = dummy_h0.half()
    dummy_c0 = dummy_c0.half()
    dummy_lidar = dummy_lidar.half()

    with torch.no_grad():
        traced = torch.jit.trace(
            wrapper,
            (dummy_image, dummy_state, dummy_h0, dummy_c0, dummy_lidar),
        )

    # Benchmark
    n_runs = 100
    t0 = time.perf_counter()
    with torch.no_grad():
        for _ in range(n_runs):
            traced(dummy_image, dummy_state, dummy_h0, dummy_c0, dummy_lidar)
    elapsed = (time.perf_counter() - t0) / n_runs * 1000
    print(f"  FP16 traced avg: {elapsed:.2f} ms/step (CPU, {n_runs} runs)")

    traced.save(out_path)
    orig_mb = os.path.getsize(checkpoint_path) / 1e6
    new_mb = os.path.getsize(out_path) / 1e6
    print(f"  Saved: {out_path}")
    print(f"  Size: {orig_mb:.1f} MB -> {new_mb:.1f} MB")

    return out_path


def main() -> None:
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = sorted(
            f for f in os.listdir(CHECKPOINTS_DIR)
            if f.endswith(".pt") and "_traced" not in f
        )

    if not targets:
        print("No checkpoints found in", CHECKPOINTS_DIR)
        return

    exported = []
    for t in targets:
        path = t if os.path.isabs(t) else os.path.join(CHECKPOINTS_DIR, t)
        if not os.path.exists(path):
            print(f"Not found: {path}")
            continue
        exported.append(export_one(path))

    print(f"\n{'=' * 60}")
    print(f"Exported {len(exported)} model(s)")
    print(f"{'=' * 60}")
    print("\nUsage on Jetson:")
    print("  model = torch.jit.load('model_traced_fp16.pt', map_location='cuda')")
    print("  actions, h, c = model(image.half(), state.half(), h0.half(), c0.half(), lidar.half())")
    print("\nNote: inputs must be FP16 (.half()). Hidden state (h, c) feeds back each step.")


if __name__ == "__main__":
    main()
