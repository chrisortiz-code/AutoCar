"""
Intel RealSense D435 camera reader — RGB + depth streaming.

Usage:
    python -m camera.reader                # auto-detect D435
    python -m camera.reader --demo         # synthetic frames (no hardware)
    python -m camera.reader --width 1280 --height 720
"""

import argparse
import math
import sys
import threading
import time

import cv2
import numpy as np

try:
    import pyrealsense2 as rs
except ImportError:
    rs = None  # type: ignore


def list_realsense_devices():
    """Return list of connected RealSense device serial numbers."""
    if rs is None:
        return []
    ctx = rs.context()
    return [d.get_info(rs.camera_info.serial_number) for d in ctx.query_devices()]


class CameraReader:
    """Background capture for Intel RealSense D435 RGB + depth."""

    def __init__(self, *, serial=None, width=640, height=480, fps=15, demo=False):
        self.serial = serial
        self.width = width
        self.height = height
        self.fps = fps
        self.demo = demo
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._connected = False
        self._error = None
        self._rgb = None       # numpy BGR uint8
        self._depth = None     # numpy uint16 (mm)
        self._depth_color = None  # numpy BGR uint8 (colorized depth)
        self._rgb_jpeg = None    # pre-encoded JPEG bytes
        self._depth_jpeg = None  # pre-encoded JPEG bytes
        self._frame_count = 0
        self._fps_actual = 0.0

    @property
    def connected(self):
        return self._connected

    @property
    def error(self):
        return self._error

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def get_frames(self):
        """Return latest frames and metadata."""
        with self._lock:
            return {
                "rgb": self._rgb.copy() if self._rgb is not None else None,
                "depth": self._depth.copy() if self._depth is not None else None,
                "depth_color": self._depth_color.copy() if self._depth_color is not None else None,
                "connected": self._connected,
                "error": self._error,
                "fps": round(self._fps_actual, 1),
                "frame_count": self._frame_count,
            }

    def get_rgb_jpeg(self, quality=80):
        """Return latest pre-encoded RGB JPEG bytes."""
        with self._lock:
            return self._rgb_jpeg

    def get_depth_jpeg(self, quality=80):
        """Return latest pre-encoded depth JPEG bytes."""
        with self._lock:
            return self._depth_jpeg

    def get_depth_points(self, step=8):
        """Return downsampled 3D point cloud from depth frame.

        Uses pinhole camera model with hardcoded D435 intrinsics for 640x480.
        Returns dict with 'points' [[x,y,z],...] in mm and 'rgb' [[r,g,b],...].
        """
        with self._lock:
            if self._depth is None:
                return {"points": [], "rgb": []}
            depth = self._depth.copy()
            rgb = self._rgb.copy() if self._rgb is not None else None

        h, w = depth.shape[:2]
        # D435 approximate intrinsics for 640x480
        fx, fy = 615.0, 615.0
        cx, cy = w / 2.0, h / 2.0

        # Build pixel coordinate grids (downsampled)
        vs = np.arange(0, h, step)
        us = np.arange(0, w, step)
        uu, vv = np.meshgrid(us, vs)
        uu = uu.ravel()
        vv = vv.ravel()

        z = depth[vv, uu].astype(np.float64)
        # Filter out zero/invalid depth
        valid = z > 0
        uu, vv, z = uu[valid], vv[valid], z[valid]

        x = (uu - cx) * z / fx
        y = (vv - cy) * z / fy

        points = np.stack([x, y, z], axis=1).tolist()

        if rgb is not None:
            # OpenCV is BGR, convert to RGB
            colors = rgb[vv, uu][:, ::-1].tolist()
        else:
            colors = [[128, 128, 128]] * len(points)

        return {"points": points, "rgb": colors}

    def get_depth_raw_jpeg(self, quality=80):
        """Return latest depth frame colorized with inferno colormap as JPEG."""
        with self._lock:
            if self._depth is None:
                return None
            norm = cv2.normalize(self._depth, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            colored = cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO)
            _, buf = cv2.imencode('.jpg', colored, [cv2.IMWRITE_JPEG_QUALITY, quality])
            return buf.tobytes()

    def _run(self):
        if self.demo:
            self._run_demo()
            return

        if rs is None:
            with self._lock:
                self._error = "pyrealsense2 not installed"
            return

        pipeline = rs.pipeline()
        config = rs.config()

        if self.serial:
            config.enable_device(self.serial)

        config.enable_stream(rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps)
        config.enable_stream(rs.stream.depth, self.width, self.height, rs.format.z16, self.fps)

        colorizer = rs.colorizer()
        colorizer.set_option(rs.option.color_scheme, 0)  # Jet colormap

        # Align depth to color frame
        align = rs.align(rs.stream.color)

        try:
            profile = pipeline.start(config)
            device = profile.get_device()
            name = device.get_info(rs.camera_info.name)
            serial = device.get_info(rs.camera_info.serial_number)

            with self._lock:
                self._connected = True
                self._error = None
            print(f"RealSense connected: {name} (S/N {serial})")
            print(f"  Streams: color {self.width}x{self.height}@{self.fps}, depth {self.width}x{self.height}@{self.fps}")

            t0 = time.perf_counter()
            fps_count = 0

            while not self._stop.is_set():
                frames = pipeline.wait_for_frames(timeout_ms=5000)
                aligned = align.process(frames)

                color_frame = aligned.get_color_frame()
                depth_frame = aligned.get_depth_frame()

                if not color_frame or not depth_frame:
                    continue

                rgb = np.asanyarray(color_frame.get_data())
                depth = np.asanyarray(depth_frame.get_data())
                depth_colored = np.asanyarray(colorizer.colorize(depth_frame).get_data())

                # Pre-encode JPEGs outside the lock
                _, rgb_buf = cv2.imencode('.jpg', rgb, [cv2.IMWRITE_JPEG_QUALITY, 70])
                _, depth_buf = cv2.imencode('.jpg', depth_colored, [cv2.IMWRITE_JPEG_QUALITY, 70])

                with self._lock:
                    self._rgb = rgb
                    self._depth = depth
                    self._depth_color = depth_colored
                    self._rgb_jpeg = rgb_buf.tobytes()
                    self._depth_jpeg = depth_buf.tobytes()
                    self._frame_count += 1

                fps_count += 1
                elapsed = time.perf_counter() - t0
                if elapsed >= 1.0:
                    self._fps_actual = fps_count / elapsed
                    fps_count = 0
                    t0 = time.perf_counter()

        except Exception as exc:
            with self._lock:
                self._connected = False
                self._error = str(exc)
            print(f"RealSense error: {exc}", file=sys.stderr)
            import traceback; traceback.print_exc()
        finally:
            pipeline.stop()
            with self._lock:
                self._connected = False

    def _run_demo(self):
        """Synthetic frames for GUI testing without hardware."""
        with self._lock:
            self._connected = True
            self._error = None
        print("Camera demo mode — synthetic frames")

        phase = 0.0
        t0 = time.perf_counter()
        fps_count = 0

        while not self._stop.is_set():
            phase += 0.05

            w, h = self.width, self.height
            y_grid, x_grid = np.mgrid[0:h, 0:w]

            # Two "objects" that move — shared between RGB and depth
            obj1_x = int(w / 2 + 100 * math.sin(phase))
            obj1_y = int(h / 2 + 80 * math.cos(phase))
            obj2_x = int(w / 4 + 60 * math.cos(phase * 0.7))
            obj2_y = int(h / 3 + 50 * math.sin(phase * 0.9))

            # Synthetic depth: objects are "bumps" closer to camera on a back wall
            wall_z = 3000  # back wall at 3m
            dist1 = np.sqrt((x_grid - obj1_x) ** 2 + (y_grid - obj1_y) ** 2).astype(np.float64)
            dist2 = np.sqrt((x_grid - obj2_x) ** 2 + (y_grid - obj2_y) ** 2).astype(np.float64)
            depth_f = np.full((h, w), wall_z, dtype=np.float64)
            # Object 1: sphere-like bump at ~1m, radius 80px
            bump1 = np.clip(1.0 - dist1 / 80.0, 0, 1)
            depth_f -= bump1 * 2000  # brings it to ~1m
            # Object 2: smaller bump at ~1.5m, radius 50px
            bump2 = np.clip(1.0 - dist2 / 50.0, 0, 1)
            depth_f -= bump2 * 1500  # brings it to ~1.5m
            depth = np.clip(depth_f, 200, 10000).astype(np.uint16)

            # Synthetic RGB: flat room color with colored objects matching depth
            rgb = np.full((h, w, 3), (60, 55, 50), dtype=np.uint8)  # grey-brown wall (BGR)
            # Object 1: green sphere
            mask1 = dist1 < 80
            rgb[mask1] = (30, 200, 50)
            # Object 2: blue box
            mask2 = dist2 < 50
            rgb[mask2] = (200, 80, 30)

            # Colorize depth for display
            depth_norm = cv2.normalize(depth, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            depth_colored = cv2.applyColorMap(depth_norm, cv2.COLORMAP_JET)

            _, rgb_buf = cv2.imencode('.jpg', rgb, [cv2.IMWRITE_JPEG_QUALITY, 70])
            _, depth_buf = cv2.imencode('.jpg', depth_colored, [cv2.IMWRITE_JPEG_QUALITY, 70])

            with self._lock:
                self._rgb = rgb
                self._depth = depth
                self._depth_color = depth_colored
                self._rgb_jpeg = rgb_buf.tobytes()
                self._depth_jpeg = depth_buf.tobytes()
                self._frame_count += 1

            fps_count += 1
            elapsed = time.perf_counter() - t0
            if elapsed >= 1.0:
                self._fps_actual = fps_count / elapsed
                fps_count = 0
                t0 = time.perf_counter()

            time.sleep(1.0 / 30)


def main():
    parser = argparse.ArgumentParser(description="Intel RealSense D435 camera reader")
    parser.add_argument("--serial", default=None, help="Device serial number")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--list-devices", action="store_true",
                        help="List connected RealSense devices and exit")
    parser.add_argument("--demo", action="store_true",
                        help="Run without hardware (synthetic frames)")
    args = parser.parse_args()

    if args.list_devices:
        devices = list_realsense_devices()
        if not devices:
            print("No RealSense devices found.")
        else:
            print("Connected RealSense devices:")
            for s in devices:
                print(f"  S/N {s}")
        return

    reader = CameraReader(
        serial=args.serial,
        width=args.width,
        height=args.height,
        fps=args.fps,
        demo=args.demo,
    )
    reader.start()
    print("Streaming frames (Ctrl+C to stop)...")
    try:
        while True:
            snap = reader.get_frames()
            if snap["error"] and not snap["connected"]:
                print(f"Error: {snap['error']}")
                break
            print(f"frames={snap['frame_count']:5d}  fps={snap['fps']:4.1f}  "
                  f"connected={snap['connected']}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        reader.stop()


if __name__ == "__main__":
    main()
