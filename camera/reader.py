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

    def __init__(self, *, serial=None, width=640, height=480, fps=30, demo=False):
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
        """Return latest RGB frame as JPEG bytes."""
        with self._lock:
            if self._rgb is None:
                return None
            _, buf = cv2.imencode('.jpg', self._rgb, [cv2.IMWRITE_JPEG_QUALITY, quality])
            return buf.tobytes()

    def get_depth_jpeg(self, quality=80):
        """Return latest colorized depth frame as JPEG bytes."""
        with self._lock:
            if self._depth_color is None:
                return None
            _, buf = cv2.imencode('.jpg', self._depth_color, [cv2.IMWRITE_JPEG_QUALITY, quality])
            return buf.tobytes()

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

                with self._lock:
                    self._rgb = rgb
                    self._depth = depth
                    self._depth_color = depth_colored
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

            # Synthetic RGB: shifting color gradient
            rgb = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            for y in range(self.height):
                r = int(127 + 127 * math.sin(y / 40.0 + phase))
                g = int(127 + 127 * math.sin(y / 60.0 + phase * 1.3))
                b = int(127 + 127 * math.sin(y / 80.0 + phase * 0.7))
                rgb[y, :] = (b, g, r)
            # Add some moving circles
            cx = int(self.width / 2 + 100 * math.sin(phase * 2))
            cy = int(self.height / 2 + 80 * math.cos(phase * 1.5))
            cv2.circle(rgb, (cx, cy), 40, (0, 255, 0), -1)
            cv2.circle(rgb, (self.width - cx, self.height - cy), 30, (255, 0, 0), -1)

            # Synthetic depth: radial gradient with moving center
            y_grid, x_grid = np.mgrid[0:self.height, 0:self.width]
            dcx = self.width / 2 + 50 * math.sin(phase)
            dcy = self.height / 2 + 50 * math.cos(phase)
            dist = np.sqrt((x_grid - dcx) ** 2 + (y_grid - dcy) ** 2)
            depth = (dist * 5 + 500).astype(np.uint16)  # mm

            # Colorize depth for display
            depth_norm = cv2.normalize(depth, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            depth_colored = cv2.applyColorMap(depth_norm, cv2.COLORMAP_JET)

            with self._lock:
                self._rgb = rgb
                self._depth = depth
                self._depth_color = depth_colored
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
