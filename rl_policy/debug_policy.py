"""Minimal headless RL policy test — no camera, no pygame, no CSRT.

Constructs a synthetic scenario where the target is straight ahead at 2m
and desired distance is 1m. The policy should obviously want to move forward.
Prints raw actions for every checkpoint found, plus runs multiple steps
to see if the LSTM state changes anything.

Usage:
    python -m rl_policy.debug_policy
"""

import os
import sys
import math
import numpy as np
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rl_policy.inference import PolicyRunner, IMG_SIZE, MAX_DEPTH_MM


def make_bbox(cx, cy, w, h):
    class BBox:
        pass
    b = BBox()
    b.cx = cx
    b.cy = cy
    b.w = w
    b.h = h
    b.confidence = 0.95
    return b


def make_depth_target_ahead(dist_m=2.0, img_w=320, img_h=240):
    """Depth frame with a flat floor and a target blob at dist_m straight ahead."""
    depth = np.full((img_h, img_w), 10000, dtype=np.uint16)  # 10m background
    # Target blob in center
    cx, cy = img_w // 2, img_h // 2
    r = 30
    yy, xx = np.ogrid[0:img_h, 0:img_w]
    mask = ((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r
    depth[mask] = int(dist_m * 1000)
    return depth


def make_rgb_green_target(img_w=320, img_h=240):
    """RGB frame with a green rectangle in the center."""
    frame = np.full((img_h, img_w, 3), 60, dtype=np.uint8)
    cx, cy = img_w // 2, img_h // 2
    r = 30
    frame[cy - r:cy + r, cx - r:cx + r] = (0, 255, 0)  # green box
    return frame


def make_lidar_clear(num_rays=100):
    """Lidar scan with everything far away."""
    points = []
    for i in range(num_rays):
        angle = i * 360.0 / num_rays
        points.append({"angle": angle, "dist_mm": 5000.0})
    return points


def run_test(ckpt_path, device="cpu"):
    print(f"\n{'=' * 70}")
    print(f"CHECKPOINT: {os.path.basename(ckpt_path)}")
    print(f"{'=' * 70}")

    runner = PolicyRunner(ckpt_path, device=device)
    variant = ("depth" if runner.model.cnn_channels == 1 else "rgbd")
    print(f"  Variant: {variant}, action_dim={runner.action_dim}, "
          f"state_dim={runner.model.state_dim}, lidar={runner.has_lidar}")
    print(f"  LSTM: hidden={runner.model.lstm_hidden_dim}, "
          f"layers={runner.model.lstm.num_layers}")
    print(f"  Compressor: {runner.model.use_compressor}")
    print(f"  Using traced: {runner.traced is not None}, fp16: {runner.fp16}")

    # Scenario: target straight ahead at 2m, desired dist 1m
    # Robot should obviously want to go forward
    target_dist = 2.0
    desired_dist = 1.0
    bbox = make_bbox(cx=0.5, cy=0.5, w=0.08, h=0.10)  # centered
    depth = make_depth_target_ahead(dist_m=target_dist)
    rgb = make_rgb_green_target()
    scan = make_lidar_clear() if runner.has_lidar else []

    print(f"\n  Scenario: target centered at {target_dist}m, desired={desired_dist}m")
    print(f"  Expected: positive forward action (move toward target)")

    # --- Inspect state vector ---
    state = runner.compute_state(bbox.cx, bbox.cy, bbox.w, bbox.h, depth, desired_dist)
    print(f"\n  State vector ({runner.model.state_dim}D): {state[0].tolist()}")

    # --- Inspect preprocessed image ---
    if runner.is_rgbd:
        img_tensor = runner.preprocess_rgbd(rgb, depth)
        print(f"  Image tensor shape: {img_tensor.shape}, "
              f"min={img_tensor.min().item():.4f}, max={img_tensor.max().item():.4f}")
    else:
        img_tensor = runner.preprocess_depth(depth)
        print(f"  Depth tensor shape: {img_tensor.shape}, "
              f"min={img_tensor.min().item():.4f}, max={img_tensor.max().item():.4f}")
        # Show the center region values
        center = img_tensor[0, 0, 28:36, 28:36]
        print(f"  Depth center 8x8 mean: {center.mean().item():.4f} "
              f"(expect ~{target_dist * 1000 / MAX_DEPTH_MM:.4f})")

    # --- Run multiple steps ---
    print(f"\n  Running 20 policy steps:")
    runner.reset()
    for step in range(20):
        actions = runner.step(rgb, depth, bbox, scan, desired_dist)
        if step < 10 or step % 5 == 0:
            act_str = ", ".join(f"{a:+.6f}" for a in actions)
            print(f"    step {step:2d}: [{act_str}]")

    # --- Test with no bbox (target lost) ---
    print(f"\n  With NO bbox (target lost):")
    runner.reset()
    actions = runner.step(rgb, depth, None, scan, desired_dist)
    act_str = ", ".join(f"{a:+.6f}" for a in actions)
    print(f"    step  0: [{act_str}]")

    # --- Test with target far right ---
    print(f"\n  Target far RIGHT (cx=0.9):")
    runner.reset()
    bbox_right = make_bbox(cx=0.9, cy=0.5, w=0.05, h=0.07)
    actions = runner.step(rgb, depth, bbox_right, scan, desired_dist)
    act_str = ", ".join(f"{a:+.6f}" for a in actions)
    print(f"    step  0: [{act_str}]")

    # --- Test with target far left ---
    print(f"\n  Target far LEFT (cx=0.1):")
    runner.reset()
    bbox_left = make_bbox(cx=0.1, cy=0.5, w=0.05, h=0.07)
    actions = runner.step(rgb, depth, bbox_left, scan, desired_dist)
    act_str = ", ".join(f"{a:+.6f}" for a in actions)
    print(f"    step  0: [{act_str}]")

    # --- Test with random noise image ---
    print(f"\n  Random noise depth (sanity check):")
    runner.reset()
    noise_depth = np.random.randint(100, 10000, (240, 320), dtype=np.uint16)
    actions = runner.step(rgb, noise_depth, bbox, scan, desired_dist)
    act_str = ", ".join(f"{a:+.6f}" for a in actions)
    print(f"    step  0: [{act_str}]")

    # --- Inspect model weights ---
    print(f"\n  Final MLP layer weights stats:")
    with torch.no_grad():
        last_layer = None
        for name, param in runner.model.mlp.named_parameters():
            last_layer = (name, param)
        if last_layer:
            name, param = last_layer
            print(f"    {name}: shape={list(param.shape)}, "
                  f"mean={param.mean().item():.6f}, std={param.std().item():.6f}, "
                  f"min={param.min().item():.6f}, max={param.max().item():.6f}")

    # --- Check if output is clamped/saturated ---
    print(f"\n  Checking action range across 50 random inputs:")
    all_actions = []
    runner.reset()
    for _ in range(50):
        rand_depth = np.random.randint(500, 5000, (240, 320), dtype=np.uint16)
        rand_cx = np.random.uniform(0.1, 0.9)
        rand_bbox = make_bbox(cx=rand_cx, cy=0.5, w=0.08, h=0.10)
        a = runner.step(rgb, rand_depth, rand_bbox, scan, desired_dist)
        all_actions.append(a)
    all_actions = np.array(all_actions)
    for dim in range(all_actions.shape[1]):
        col = all_actions[:, dim]
        labels = ["fwd", "strafe", "yaw"] if all_actions.shape[1] == 3 else ["fwd", "yaw"]
        lbl = labels[dim] if dim < len(labels) else f"dim{dim}"
        print(f"    {lbl}: min={col.min():+.6f}, max={col.max():+.6f}, "
              f"mean={col.mean():+.6f}, std={col.std():.6f}")


def main():
    ckpt_dir = os.path.join(os.path.dirname(__file__), "checkpoints")
    if not os.path.isdir(ckpt_dir):
        print(f"No checkpoints dir at {ckpt_dir}")
        return

    candidates = sorted(
        f for f in os.listdir(ckpt_dir)
        if f.endswith(".pt") and "_traced" not in f
    )
    traced_only = sorted(
        f.replace("_traced_fp16.pt", ".pt")
        for f in os.listdir(ckpt_dir)
        if f.endswith("_traced_fp16.pt")
        and f.replace("_traced_fp16.pt", ".pt") not in set(candidates)
    )
    all_ckpts = candidates + traced_only

    if not all_ckpts:
        print("No checkpoints found!")
        return

    print(f"Found {len(all_ckpts)} checkpoint(s): {all_ckpts}")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")

    for name in all_ckpts:
        path = os.path.join(ckpt_dir, name)
        try:
            run_test(path, device=device)
        except Exception as e:
            print(f"\n  ERROR loading {name}: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
