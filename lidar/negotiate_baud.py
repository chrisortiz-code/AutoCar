"""
RPLIDAR baud rate negotiation — switch an S2 to a lower baud rate.

The RPLIDAR S2 (firmware >= 1.30) supports runtime baud rate negotiation.
This lets you run the lidar at e.g. 256000 baud instead of 1Mbps, which
is reliable on Jetson GPIO UART where 1Mbps is not.

The new baud rate persists only for the current power cycle — on reboot
the lidar reverts to its default 1Mbps.

Protocol (from Slamtec SDK):
  1. Connect at the DESIRED baud rate (not the default)
  2. Send 0x41 magic bytes continuously for ~1.5 seconds
  3. The lidar measures the baud rate from byte timing
  4. It replies with the detected baud rate (4 bytes, little-endian)
  5. Communication continues at the new rate

Usage:
    python -m lidar.negotiate_baud                        # 256000 on GPIO
    python -m lidar.negotiate_baud --baudrate 115200      # even slower
    python -m lidar.negotiate_baud --port /dev/ttyUSB0    # USB adapter
"""

import argparse
import struct
import time

import serial

MAGIC_BYTE = 0x41
NEGOTIATE_DURATION = 1.5  # seconds
DEFAULT_TARGET_BAUD = 256_000
JETSON_UART = "/dev/ttyTHS1"


def negotiate(port, target_baud, verbose=True):
    """Negotiate a new baud rate with the RPLIDAR.

    Returns the baud rate detected by the lidar, or None on failure.
    """
    if verbose:
        print(f"Opening {port} at {target_baud} baud...")

    ser = serial.Serial(port, baudrate=target_baud, timeout=1)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    time.sleep(0.1)

    # Send magic bytes for ~1.5 seconds
    magic_block = bytes([MAGIC_BYTE] * 16)
    start = time.perf_counter()

    if verbose:
        print(f"Sending magic bytes (0x{MAGIC_BYTE:02X}) for {NEGOTIATE_DURATION}s...")

    while time.perf_counter() - start < NEGOTIATE_DURATION:
        ser.write(magic_block)

        # Check if lidar responded early
        if ser.in_waiting > 0:
            break

    # Wait for 4-byte response (detected baud rate)
    time.sleep(0.2)
    response = ser.read(4)

    if len(response) < 4:
        if verbose:
            print(f"No response from lidar (got {len(response)} bytes)")
        ser.close()
        return None

    detected_baud = struct.unpack('<I', response)[0]
    error_pct = abs(detected_baud - target_baud) * 100.0 / target_baud

    if verbose:
        print(f"Lidar detected: {detected_baud} bps (error: {error_pct:.2f}%)")
        if error_pct > 5.0:
            print("WARNING: BPS error > 5%, communication may be unreliable")
        else:
            print("Negotiation successful!")

    ser.close()
    return detected_baud


def verify(port, baud, verbose=True):
    """Verify the lidar responds at the new baud rate by requesting device info."""
    if verbose:
        print(f"\nVerifying communication at {baud} baud...")

    try:
        from pyrplidar import PyRPlidar
        lidar = PyRPlidar()
        lidar.connect(port=port, baudrate=baud, timeout=3)
        info = lidar.get_info()
        health = lidar.get_health()
        if verbose:
            print(f"  info: {info}")
            print(f"  health: {health}")
            print(f"Lidar responding at {baud} baud!")
        lidar.disconnect()
        return True
    except Exception as e:
        if verbose:
            print(f"  verification failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Negotiate a lower baud rate with RPLIDAR S2")
    parser.add_argument("--port", default=JETSON_UART,
                        help=f"Serial port (default: {JETSON_UART})")
    parser.add_argument("--baudrate", type=int, default=DEFAULT_TARGET_BAUD,
                        help=f"Target baud rate, 115200-512000 (default: {DEFAULT_TARGET_BAUD})")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip verification step")
    args = parser.parse_args()

    if args.baudrate < 115200 or args.baudrate > 512000:
        parser.error("Baud rate must be between 115200 and 512000")

    # First, stop the lidar if it's running at the default baud rate
    print("Stopping lidar (at default 1Mbps)...")
    try:
        ser = serial.Serial(args.port, baudrate=1_000_000, timeout=1)
        ser.write(b'\xA5\x25')  # RPLIDAR STOP command
        time.sleep(0.1)
        ser.reset_input_buffer()
        ser.close()
    except Exception as e:
        print(f"  (could not send stop at 1Mbps: {e})")

    time.sleep(0.3)

    # Negotiate
    detected = negotiate(args.port, args.baudrate)
    if detected is None:
        print("\nNegotiation failed. The lidar may not support baud rate negotiation.")
        print("S2 firmware must be >= 1.30.")
        return

    # Verify
    if not args.no_verify:
        if verify(args.port, args.baudrate):
            print(f"\nReady! Run the viewer with:")
            print(f"  python -m lidar.viewer --gpio --baudrate {args.baudrate}")
        else:
            print("\nNegotiation seemed to work but verification failed.")
            print("Try a different baud rate or check wiring.")


if __name__ == "__main__":
    main()
