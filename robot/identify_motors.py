"""
Motor identification script.
Spins each ODrive node one at a time so you can see which wheel moves.
After identifying, update the MOTORS dict in can_bus.py if needed.

Usage:  python robot/identify_motors.py
"""

import time
from can_bus import MecanumCAN, MOTORS

SPIN_VEL = 2.0    # turns/s — slow enough to be safe
SPIN_TIME = 2.0   # seconds per motor


def main():
    mc = MecanumCAN()
    mc.connect()

    if not mc.connected:
        print("No motors found!")
        return

    print(f"\nFound {len(mc.connected)} motors: {sorted(mc.connected)}")
    print(f"\nCurrent mapping in can_bus.py:")
    for nid in sorted(mc.connected):
        m = MOTORS[nid]
        print(f"  Node {nid} = {m['role']}  (dir={m['dir']:+d})")

    print(f"\n--- Spinning each motor for {SPIN_TIME}s at {SPIN_VEL} turns/s ---")
    print("Watch which wheel spins!\n")

    try:
        for nid in sorted(mc.connected):
            role = MOTORS[nid]["role"]
            print(f">>> Node {nid} (mapped as {role}) — spinning now...")
            mc.arm(nid)
            mc.set_vel(nid, SPIN_VEL)
            time.sleep(SPIN_TIME)
            mc.stop_motor(nid)
            print(f"    Node {nid} done.\n")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nAborted")
    finally:
        mc.stop_all()
        mc.shutdown()

    print("Done. Update MOTORS in robot/can_bus.py if any mappings are wrong.")


if __name__ == "__main__":
    main()
