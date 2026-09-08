"""
Motor identification script for USB-connected ODrives.
Discovers all ODrives, spins each one so you can see which wheel moves,
then saves the serial-to-role mapping in motor_config.json.

Usage:  python robot/identify_motors.py
"""

import time
from usb_odrive import MecanumUSB, ALL_ROLES, save_config
from odrive.enums import (
    AXIS_STATE_IDLE,
    AXIS_STATE_CLOSED_LOOP_CONTROL,
    CONTROL_MODE_VELOCITY_CONTROL,
    INPUT_MODE_PASSTHROUGH,
)

SPIN_VEL = 2.0    # turns/s — slow enough to be safe
SPIN_TIME = 2.0   # seconds per motor


def main():
    mc = MecanumUSB()
    found = mc.discover_all(count=4, timeout=15)

    if not found:
        print("No ODrives found!")
        return

    print(f"\nFound {len(found)} ODrives:")
    for i, (sn, odrv) in enumerate(found):
        print(f"  [{i}] serial={sn}")

    print(f"\n--- Spinning each ODrive for {SPIN_TIME}s at {SPIN_VEL} turns/s ---")
    print("Watch which wheel spins!\n")

    mapping = {}
    try:
        for i, (sn, odrv) in enumerate(found):
            ax = odrv.axis0
            print(f">>> ODrive [{i}] serial={sn} — spinning now...")
            ax.controller.config.control_mode = CONTROL_MODE_VELOCITY_CONTROL
            ax.controller.config.input_mode = INPUT_MODE_PASSTHROUGH
            ax.requested_state = AXIS_STATE_CLOSED_LOOP_CONTROL
            time.sleep(0.2)
            ax.controller.input_vel = SPIN_VEL
            time.sleep(SPIN_TIME)
            ax.controller.input_vel = 0.0
            time.sleep(0.1)
            ax.requested_state = AXIS_STATE_IDLE
            print(f"    ODrive [{i}] done.")

            while True:
                role = input(f"    Which wheel was that? ({'/'.join(ALL_ROLES)}): ").strip().upper()
                if role in ALL_ROLES and role not in mapping.values():
                    break
                if role in mapping.values():
                    print(f"    {role} already assigned, pick another.")
                else:
                    print(f"    Invalid. Choose from: {', '.join(ALL_ROLES)}")

            mapping[role] = sn
            print(f"    Mapped {role} -> serial {sn}\n")
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nAborted")
        for sn, odrv in found:
            try:
                odrv.axis0.controller.input_vel = 0.0
                odrv.axis0.requested_state = AXIS_STATE_IDLE
            except Exception:
                pass
        return

    for sn, odrv in found:
        try:
            odrv.axis0.controller.input_vel = 0.0
            odrv.axis0.requested_state = AXIS_STATE_IDLE
        except Exception:
            pass

    if len(mapping) == len(found):
        save_config(mapping)
        print("\nMapping saved! You can now run universal_receiver.py")
    else:
        print(f"\nOnly mapped {len(mapping)}/{len(found)} motors. Run again to complete.")


if __name__ == "__main__":
    main()
