"""Benchmark RL policy inference on the current device.

Measures isolated model forward-pass latency and full preprocessing
pipeline latency. Run on Jetson to find your real max Hz.

Usage:
    python -m rl_policy.benchmark
    python -m rl_policy.benchmark --checkpoint rl_policy/checkpoints/rgbd_find.pt
    python -m rl_policy.benchmark --warmup 20 --iterations 500
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rl_policy.model import load_actor
from rl_policy.inference import PolicyRunner, IMG_SIZE, NUM_LIDAR_RAYS, MAX_DEPTH_MM

CKPT_DIR = os.path.join(os.path.dirname(__file__), "checkpoints")
DEFAULT_MODEL = "depthonly_curriculum.pt"


def find_default_checkpoint() -> str:
    preferred = os.path.join(CKPT_DIR, DEFAULT_MODEL)
    preferred_traced = preferred.replace(".pt", "_traced_fp16.pt")
    if os.path.isfile(preferred) or os.path.isfile(preferred_traced):
        return preferred
    candidates = sorted(
        f for f in os.listdir(CKPT_DIR)
        if f.endswith(".pt") and "_traced" not in f
    )
    if candidates:
        return os.path.join(CKPT_DIR, candidates[0])
    raise FileNotFoundError(f"No checkpoints in {CKPT_DIR}")


def percentile(times: list[float], p: int) -> float:
    idx = int(len(times) * p / 100)
    return sorted(times)[min(idx, len(times) - 1)]


def print_stats(label: str, times_ms: list[float]) -> None:
    times_ms.sort()
    mean = sum(times_ms) / len(times_ms)
    p50 = percentile(times_ms, 50)
    p95 = percentile(times_ms, 95)
    p99 = percentile(times_ms, 99)
    max_hz = 1000.0 / p95 if p95 > 0 else float("inf")
    print(f"\n  {label}")
    print(f"    mean:  {mean:7.2f} ms")
    print(f"    p50:   {p50:7.2f} ms")
    print(f"    p95:   {p95:7.2f} ms  -> max sustainable: {max_hz:.0f} Hz")
    print(f"    p99:   {p99:7.2f} ms")
    print(f"    min:   {min(times_ms):7.2f} ms")
    print(f"    max:   {max(times_ms):7.2f} ms")


def bench_model_forward(
    checkpoint: str, device: str, warmup: int, iterations: int,
) -> None:
    """Benchmark raw model forward pass with dummy tensors."""
    print(f"\n{'=' * 60}")
    print("1) Isolated model forward pass (dummy tensors)")
    print(f"{'=' * 60}")

    traced_path = checkpoint.replace(".pt", "_traced_fp16.pt")
    has_raw = os.path.exists(checkpoint)
    has_traced = os.path.exists(traced_path)

    if has_raw:
        actor = load_actor(checkpoint, device=device)
    elif has_traced:
        from rl_policy.inference import PolicyRunner
        actor = PolicyRunner._build_model_from_traced(traced_path, device)
    else:
        raise FileNotFoundError(f"Neither {checkpoint} nor {traced_path} found")

    nl = actor.lstm.num_layers
    hd = actor.lstm_hidden_dim
    use_lidar = actor.use_lidar

    img = torch.zeros(1, actor.cnn_channels, IMG_SIZE, IMG_SIZE, device=device)
    state = torch.zeros(1, actor.state_dim, device=device)
    h0 = torch.zeros(nl, 1, hd, device=device)
    c0 = torch.zeros(nl, 1, hd, device=device)
    lidar = torch.zeros(1, NUM_LIDAR_RAYS, device=device) if use_lidar else None

    for label, run_traced in [("FP32 eager", False), ("FP16 traced", True)]:
        if run_traced and not has_traced:
            print(f"\n  {label}: skipped (no traced export found)")
            continue
        if not run_traced and not has_raw:
            print(f"\n  {label}: skipped (raw checkpoint not present)")
            continue

        if run_traced:
            model = torch.jit.load(traced_path, map_location=device)
            model.eval()
            _img = img.half()
            _state = state.half()
            _h0 = h0.half()
            _c0 = c0.half()
            _lidar = lidar.half() if lidar is not None else torch.zeros(1, 360, device=device).half()
        else:
            model = actor
            _img, _state, _h0, _c0 = img, state, h0, c0
            _lidar = lidar

        times_ms = []
        with torch.inference_mode():
            for i in range(warmup + iterations):
                if device == "cuda":
                    torch.cuda.synchronize()
                t0 = time.perf_counter()

                if run_traced:
                    actions, _h0, _c0 = model(_img, _state, _h0, _c0, _lidar)
                else:
                    actions, (_h0, _c0) = model(_img, _state, (_h0, _c0), _lidar)
                _h0 = _h0.detach()
                _c0 = _c0.detach()

                if device == "cuda":
                    torch.cuda.synchronize()
                elapsed = (time.perf_counter() - t0) * 1000.0

                if i >= warmup:
                    times_ms.append(elapsed)

        print_stats(label, times_ms)


def bench_full_pipeline(
    checkpoint: str, device: str, warmup: int, iterations: int,
) -> None:
    """Benchmark full preprocessing + inference (no camera/tracker)."""
    print(f"\n{'=' * 60}")
    print("2) Full pipeline: preprocess + inference (synthetic frames)")
    print(f"{'=' * 60}")

    runner = PolicyRunner(checkpoint, device=device)

    h, w = 480, 640
    fake_rgb = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
    fake_depth = np.random.randint(100, 5000, (h, w), dtype=np.uint16)
    fake_scan = [{"angle": i * 360.0 / 500, "dist_mm": 1000 + i} for i in range(500)]

    class FakeBBox:
        cx, cy, w, h, confidence = 0.5, 0.5, 0.15, 0.2, 0.9

    bbox = FakeBBox()
    desired_dist = 1.0

    times_ms = []
    preprocess_ms = []
    inference_ms = []

    for i in range(warmup + iterations):
        if device == "cuda":
            torch.cuda.synchronize()
        t0 = time.perf_counter()

        # Preprocessing
        if runner.is_rgbd:
            image = runner.preprocess_rgbd(fake_rgb, fake_depth)
        else:
            image = runner.preprocess_depth(fake_depth)
        state = runner.compute_state(bbox.cx, bbox.cy, bbox.w, bbox.h, fake_depth, desired_dist)
        lidar = runner.preprocess_lidar(fake_scan) if runner.has_lidar else None

        if device == "cuda":
            torch.cuda.synchronize()
        t_pre = time.perf_counter()

        # Inference
        with torch.inference_mode():
            if runner.traced is not None:
                lidar_t = runner._preprocess_lidar_traced(fake_scan)
                dtype = torch.float16 if runner.fp16 else torch.float32
                if runner.fp16:
                    image = image.half()
                    state = state.half()
                    lidar_t = lidar_t.half()
                nl = runner.model.lstm.num_layers
                hd = runner.model.lstm_hidden_dim
                if runner.hidden is None:
                    h0 = torch.zeros(nl, 1, hd, device=runner.device, dtype=dtype)
                    c0 = torch.zeros(nl, 1, hd, device=runner.device, dtype=dtype)
                else:
                    h0, c0 = runner.hidden
                actions, h_new, c_new = runner.traced(image, state, h0, c0, lidar_t)
                runner.hidden = (h_new.detach(), c_new.detach())
            else:
                actions, hidden = runner.model(image, state, runner.hidden, lidar)
                runner.hidden = (hidden[0].detach(), hidden[1].detach())

        if device == "cuda":
            torch.cuda.synchronize()
        t_end = time.perf_counter()

        if i >= warmup:
            times_ms.append((t_end - t0) * 1000.0)
            preprocess_ms.append((t_pre - t0) * 1000.0)
            inference_ms.append((t_end - t_pre) * 1000.0)

    print_stats("Preprocessing only", preprocess_ms)
    print_stats("Inference only", inference_ms)
    print_stats("Total (preprocess + inference)", times_ms)


def bench_csrt(warmup: int, iterations: int) -> None:
    """Benchmark CSRT tracker independently."""
    import cv2

    print(f"\n{'=' * 60}")
    print("3) CSRT tracker (synthetic frames)")
    print(f"{'=' * 60}")

    h, w = 480, 640
    tracker = cv2.TrackerCSRT.create() if hasattr(cv2, "TrackerCSRT") else cv2.TrackerCSRT_create()

    frame = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
    tracker.init(frame, (280, 200, 80, 80))

    times_ms = []
    for i in range(warmup + iterations):
        frame = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
        t0 = time.perf_counter()
        tracker.update(frame)
        elapsed = (time.perf_counter() - t0) * 1000.0
        if i >= warmup:
            times_ms.append(elapsed)

    print_stats("CSRT update", times_ms)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark RL policy inference")
    parser.add_argument("--checkpoint", default="", help="Checkpoint path (default: auto)")
    parser.add_argument("--warmup", type=int, default=10, help="Warmup iterations")
    parser.add_argument("--iterations", type=int, default=200, help="Benchmark iterations")
    parser.add_argument("--no-csrt", action="store_true", help="Skip CSRT benchmark")
    args = parser.parse_args()

    checkpoint = args.checkpoint or find_default_checkpoint()
    device = "cuda" if torch.cuda.is_available() else "cpu"

    print(f"Device:      {device}")
    if device == "cuda":
        print(f"GPU:         {torch.cuda.get_device_name(0)}")
        mem = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"GPU memory:  {mem:.1f} GB")
    print(f"Checkpoint:  {os.path.basename(checkpoint)}")
    print(f"Warmup:      {args.warmup}")
    print(f"Iterations:  {args.iterations}")

    bench_model_forward(checkpoint, device, args.warmup, args.iterations)
    bench_full_pipeline(checkpoint, device, args.warmup, args.iterations)

    if not args.no_csrt:
        try:
            bench_csrt(args.warmup, args.iterations)
        except Exception as e:
            print(f"\n  CSRT benchmark skipped: {e}")

    print(f"\n{'=' * 60}")
    print("Summary")
    print(f"{'=' * 60}")
    print("Run this on the Jetson to get real deployment numbers.")
    print("Monitor thermals with: tegrastats")
    print("For sustained load test, use --iterations 2000")


if __name__ == "__main__":
    main()
