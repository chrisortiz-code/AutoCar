"""
RPLIDAR reader — S2 defaults (1 Mbps serial over USB adapter).

Usage:
    python -m lidar.reader --list-ports
    python -m lidar.reader --port COM3
    python -m lidar.reader --gpio          # Use Jetson UART pins instead of USB
"""

# Jetson hardware UART for GPIO pin connection (TX/RX on 40-pin header)
JETSON_UART = "/dev/ttyTHS1"

import argparse
import math
import sys
import threading
import time

try:
    import serial.tools.list_ports
except ImportError:
    serial = None  # type: ignore


def _patch_pyrplidar():
    """Fix pyrplidar dense capsule bugs:
    1. DenseCabin byte order is big-endian but protocol is little-endian.
    2. _parse_capsule applies << 2 to dense distances (already in mm).
    """
    try:
        import pyrplidar as _mod
        # Fix byte order: protocol is little-endian
        _orig_cabin_init = _mod.PyRPlidarDenseCabin.__init__

        def _cabin_init(self, raw_bytes):
            self.distance = raw_bytes[0] + (raw_bytes[1] << 8)

        _mod.PyRPlidarDenseCabin.__init__ = _cabin_init

        # Fix _parse_capsule: don't << 2 for dense distances
        _orig_parse = _mod.PyRPlidarScanDenseCapsule._parse_capsule

        @classmethod
        def _fixed_parse(cls, capsule_prev, capsule_current):
            nodes = []
            currentStartAngle_q8 = capsule_current.start_angle_q6 << 2
            prevStartAngle_q8 = capsule_prev.start_angle_q6 << 2
            diffAngle_q8 = currentStartAngle_q8 - prevStartAngle_q8
            if prevStartAngle_q8 > currentStartAngle_q8:
                diffAngle_q8 += (360 << 8)
            angleInc_q16 = (diffAngle_q8 << 8) // 40
            currentAngle_raw_q16 = prevStartAngle_q8 << 8
            for pos in range(len(capsule_prev.cabins)):
                syncBit = 1 if (((currentAngle_raw_q16 + angleInc_q16) % (360 << 16)) < angleInc_q16) else 0
                angle_q6 = currentAngle_raw_q16 >> 10
                if angle_q6 < 0:
                    angle_q6 += (360 << 6)
                if angle_q6 >= (360 << 6):
                    angle_q6 -= (360 << 6)
                currentAngle_raw_q16 += angleInc_q16
                # Dense cabin distance is already in mm — no << 2
                dist_q2 = capsule_prev.cabins[pos].distance * 4  # store as q2 for MeasurementHQ
                node = _mod.PyRPlidarMeasurementHQ(syncBit, angle_q6, dist_q2)
                nodes.append(node)
            return nodes

        _mod.PyRPlidarScanDenseCapsule._parse_capsule = _fixed_parse
    except ImportError:
        pass


_patch_pyrplidar()

# S2 uses 1M baud; A1/A2=115200, A3/S1=256000
MODEL_BAUD = {
    "s2": 1_000_000,
    "s1": 256_000,
    "a3": 256_000,
    "a1": 115_200,
    "a2": 115_200,
}

MODEL_SCAN_TYPE = {
    "s2": "express",
    "s1": "express",
    "a3": "express",
    "a1": "normal",
    "a2": "normal",
}


def list_serial_ports():
    """Return available serial port device names."""
    if serial is None:
        return []
    return [p.device for p in serial.tools.list_ports.comports()]


class LidarReader:
    """Background scan reader for RPLIDAR devices."""

    def __init__(self, port, *, baudrate=1_000_000, scan_type='express', demo=False, motor_pwm=660):
        self.port = port
        self.baudrate = baudrate
        self.scan_type = scan_type
        self.demo = demo
        self.motor_pwm = motor_pwm
        self._lidar = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._connected = False
        self._error = None
        self._scan = []  # list of {angle, dist_mm, quality}
        self._scan_hz = 0.0
        self._last_scan_time = 0.0

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
        self._disconnect()

    def get_scan(self):
        """Return a snapshot of the latest scan and metadata."""
        with self._lock:
            points = list(self._scan)
            return {
                "points": points,
                "count": len(points),
                "scan_hz": round(self._scan_hz, 1),
                "connected": self._connected,
                "port": self.port,
                "error": self._error,
            }

    def _disconnect(self):
        lidar = self._lidar
        self._lidar = None
        if lidar is None:
            return
        try:
            lidar.stop()
        except Exception:
            pass
        try:
            lidar.set_motor_pwm(0)
        except Exception:
            pass
        # Flush any remaining serial data before closing
        try:
            if hasattr(lidar, '_serial') and lidar._serial:
                lidar._serial.reset_input_buffer()
                lidar._serial.reset_output_buffer()
        except Exception:
            pass
        time.sleep(0.1)
        try:
            lidar.disconnect()
        except Exception:
            pass
        # Force-close the underlying serial port if pyrplidar didn't
        try:
            if hasattr(lidar, '_serial') and lidar._serial and lidar._serial.is_open:
                lidar._serial.close()
        except Exception:
            pass

    def _run(self):
        if self.demo:
            self._run_demo()
            return
        try:
            from pyrplidar import PyRPlidar
        except ImportError as exc:
            with self._lock:
                self._error = f"Missing pyrplidar package: {exc}"
            return

        lidar = PyRPlidar()
        try:
            lidar.connect(port=self.port, baudrate=self.baudrate, timeout=3)
            # Flush any stale data from previous session
            if hasattr(lidar, '_serial') and lidar._serial:
                lidar._serial.reset_input_buffer()
                lidar._serial.reset_output_buffer()
            time.sleep(0.2)
            self._lidar = lidar

            # Retry get_info in case of sync errors from stale data
            for attempt in range(3):
                try:
                    info = lidar.get_info()
                    break
                except Exception:
                    if hasattr(lidar, '_serial') and lidar._serial:
                        lidar._serial.reset_input_buffer()
                    time.sleep(0.3)
            else:
                raise RuntimeError("Failed to sync with lidar after 3 attempts")
            health = lidar.get_health()
            with self._lock:
                self._connected = True
                self._error = None
            print(f"RPLIDAR connected on {self.port} @ {self.baudrate}")
            print(f"  info={info}  health={health}")

            # S2 supports: mode 0 = Standard (broken), mode 1 = DenseBoost (correct)
            # Try DenseBoost first, fall back to typical
            scan_mode = 1  # DenseBoost
            try:
                typical_id = lidar.get_scan_mode_typical()
                print(f"  device typical mode: {typical_id}, using mode: {scan_mode}")
            except Exception:
                print(f"  using mode: {scan_mode}")

            lidar.set_motor_pwm(self.motor_pwm)
            print(f"  motor PWM: {self.motor_pwm}")
            time.sleep(1.0)

            resync_count = 0
            while not self._stop.is_set():
                try:
                    self._run_scan_loop(lidar, scan_mode)
                    resync_count = 0
                except Exception as e:
                    resync_count += 1
                    print(f"  scan restart failed ({e}), attempt {resync_count}...")
                    self._resync(lidar)
                    if resync_count >= 10:
                        raise RuntimeError(f"Too many resync failures: {e}")
        except Exception as exc:
            with self._lock:
                self._connected = False
                self._error = str(exc)
            print(f"RPLIDAR error: {exc}", file=sys.stderr)
            import traceback; traceback.print_exc()
        finally:
            self._disconnect()
            with self._lock:
                self._connected = False

    def _run_scan_loop(self, lidar, scan_mode):
        """Run one scan session. Returns on corruption to allow restart."""
        scan_gen = lidar.start_scan_express(scan_mode)

        NUM_SLOTS = 4500
        buf = [None] * NUM_SLOTS
        scan_times = []
        prev_angle = -1.0
        update_count = 0
        # Corruption detection
        false_revs = 0  # consecutive impossibly fast revolutions
        last_rev_time = 0.0

        try:
            for measurement in scan_gen():
                if self._stop.is_set():
                    return

                angle = measurement.angle
                dist = measurement.distance
                quality = measurement.quality

                now = time.perf_counter()

                if 0 < dist < 30000:
                    slot = int(angle * NUM_SLOTS / 360.0) % NUM_SLOTS
                    buf[slot] = (round(angle, 2), int(dist), int(quality), now)
                    update_count += 1

                # Detect full revolution to update Hz counter
                if prev_angle > 300 and angle < 60:
                    if last_rev_time > 0:
                        rev_dt = now - last_rev_time
                        rev_hz = 1.0 / rev_dt if rev_dt > 0 else 999

                        # S2 max spin is ~15Hz. Anything above 30Hz is corrupt data.
                        if rev_hz > 30:
                            false_revs += 1
                            if false_revs >= 3:
                                print(f"  corrupt data detected ({rev_hz:.0f}Hz), re-syncing...")
                                self._resync(lidar)
                                return  # restart scan loop
                        else:
                            false_revs = 0
                            scan_times.append(rev_dt)
                            if len(scan_times) > 20:
                                scan_times.pop(0)
                            self._scan_hz = 1.0 / (sum(scan_times) / len(scan_times))

                    last_rev_time = now

                prev_angle = angle

                # Push snapshot to viewer every ~360 points
                if update_count >= 360:
                    update_count = 0
                    cutoff = now - 0.5
                    snapshot = [
                        {"angle": b[0], "dist_mm": b[1], "quality": b[2]}
                        for b in buf if b is not None and b[3] > cutoff
                    ]
                    with self._lock:
                        self._scan = snapshot
        except Exception as e:
            print(f"  scan error: {e}, re-syncing...")
            self._resync(lidar)

    def _resync(self, lidar):
        """Stop scan, flush serial buffer, and pause before restarting."""
        try:
            lidar.stop()
        except Exception:
            pass
        # Drain and flush repeatedly to clear all in-flight data
        ser = getattr(lidar, '_serial', None)
        if ser:
            for _ in range(5):
                try:
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()
                except Exception:
                    pass
                time.sleep(0.1)
            # Read and discard anything still arriving
            try:
                ser.timeout = 0.1
                while ser.read(4096):
                    pass
                ser.timeout = 3
            except Exception:
                pass
        time.sleep(0.5)

    def _run_demo(self):
        """Synthetic scan for GUI testing without hardware."""
        with self._lock:
            self._connected = True
            self._error = None
        t0 = time.perf_counter()
        phase = 0.0
        while not self._stop.is_set():
            phase += 0.08
            points = []
            for i in range(360):
                angle = float(i)
                base = 1200 + 400 * math.sin(math.radians(angle * 3 + phase * 40))
                wobble = 180 * math.sin(math.radians(angle * 8 + phase * 90))
                dist = max(200, int(base + wobble))
                quality = 40 + int(20 * abs(math.sin(math.radians(angle + phase * 30))))
                points.append({"angle": angle, "dist_mm": dist, "quality": quality})
            with self._lock:
                self._scan = points
                self._scan_hz = 10.0
            elapsed = time.perf_counter() - t0
            if elapsed < 0.1:
                time.sleep(0.1 - elapsed)
            t0 = time.perf_counter()


def main():
    parser = argparse.ArgumentParser(description="RPLIDAR S2 serial reader")
    parser.add_argument("--port", default=None, help="Serial port (e.g. COM3, /dev/ttyUSB0)")
    parser.add_argument("--model", default="s2", choices=sorted(MODEL_BAUD),
                        help="Lidar model for default baud rate")
    parser.add_argument("--baudrate", type=int, default=None,
                        help="Override serial baud rate")
    parser.add_argument("--list-ports", action="store_true",
                        help="List available serial ports and exit")
    parser.add_argument("--gpio", action="store_true",
                        help=f"Use Jetson UART GPIO pins ({JETSON_UART}) instead of USB")
    parser.add_argument("--motor-pwm", type=int, default=660,
                        help="Motor PWM (lower = slower spin, default 660)")
    parser.add_argument("--demo", action="store_true",
                        help="Run without hardware (synthetic scan)")
    args = parser.parse_args()

    if args.list_ports:
        ports = list_serial_ports()
        if not ports:
            print("No serial ports found.")
        else:
            print("Available serial ports:")
            for p in ports:
                print(f"  {p}")
        return

    baud = args.baudrate or MODEL_BAUD[args.model]
    scan_type = MODEL_SCAN_TYPE[args.model]
    port = args.port
    if args.gpio:
        port = port or JETSON_UART
        print(f"GPIO mode: using UART {port}")
    elif not args.demo and not port:
        ports = list_serial_ports()
        if len(ports) == 1:
            port = ports[0]
            print(f"Auto-selected port: {port}")
        else:
            parser.error("Specify --port, --gpio, or use --demo. Run with --list-ports to see devices.")

    reader = LidarReader(port or "demo", baudrate=baud, scan_type=scan_type, demo=args.demo, motor_pwm=args.motor_pwm)
    reader.start()
    print("Streaming scans (Ctrl+C to stop)...")
    try:
        while True:
            snap = reader.get_scan()
            if snap["error"] and not snap["connected"]:
                print(f"Error: {snap['error']}")
                break
            dists = [p["dist_mm"] for p in snap["points"]]
            if dists:
                print(f"{snap['count']:4d} pts  {snap['scan_hz']:4.1f} Hz  "
                      f"range {min(dists)/1000:.2f}-{max(dists)/1000:.2f} m")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        reader.stop()


if __name__ == "__main__":
    main()
