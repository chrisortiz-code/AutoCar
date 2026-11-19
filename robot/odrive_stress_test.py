"""
ODrive S1 Solo Wheel Stress Test (FW 0.6.x)
=============================================
Connect one ODrive S1 via USB, run this script.
It will run through each stress test and report what tripped.

Usage: python robot/odrive_stress_test.py
"""

import odrive
from odrive.enums import *
import time
import sys


def safe_get(obj, *attrs):
    """Try multiple attribute paths, return first that works."""
    for attr_path in attrs:
        try:
            val = obj
            for part in attr_path.split("."):
                val = getattr(val, part)
            return val
        except AttributeError:
            continue
    return "N/A"


def get_vel(odrv):
    """Get velocity estimate, trying FW 0.6.x paths."""
    return safe_get(odrv.axis0, "vel_estimate", "encoder_estimator.vel_estimate", "encoder.vel_estimate")


def wait_for_speed(odrv, target, timeout=5):
    """Wait until motor roughly reaches target velocity."""
    start = time.time()
    while time.time() - start < timeout:
        vel = get_vel(odrv)
        if isinstance(vel, (int, float)) and abs(vel - target) < abs(target) * 0.2 + 1:
            return True
        time.sleep(0.1)
    return False


def check_errors(odrv):
    """Check for any errors, return dict of error sources."""
    errors = {}
    ax = odrv.axis0
    # FW 0.6.x uses active_errors on axis, disarm_reason for why it stopped
    try:
        if ax.active_errors != 0:
            errors["active_errors"] = ax.active_errors
    except AttributeError:
        pass
    try:
        if ax.disarm_reason != 0:
            errors["disarm_reason"] = ax.disarm_reason
    except AttributeError:
        pass
    # Fallback for older 0.6.x builds
    for attr in ["error"]:
        try:
            val = getattr(ax, attr)
            if val != 0:
                errors[f"axis.{attr}"] = val
        except AttributeError:
            pass
    return errors


def print_header(name):
    print(f"\n{'='*50}")
    print(f"  TEST: {name}")
    print(f"{'='*50}")


def print_config(odrv):
    """Print all relevant config values for FW 0.6.x."""
    ax = odrv.axis0
    print("\n--- Current Config ---")
    print(f"  current_soft_max         = {safe_get(ax, 'config.motor.current_soft_max', 'motor.config.current_soft_max', 'motor.config.current_lim')} A")
    print(f"  current_hard_max         = {safe_get(ax, 'config.motor.current_hard_max', 'motor.config.current_hard_max', 'motor.config.current_lim_margin')} A")
    print(f"  vel_limit                = {safe_get(ax, 'controller.config.vel_limit')} turns/s")
    print(f"  vel_ramp_rate            = {safe_get(ax, 'controller.config.vel_ramp_rate')} turns/s^2")
    print(f"  dc_bus_overvoltage_trip  = {safe_get(odrv, 'config.dc_bus_overvoltage_trip_level')} V")
    print(f"  dc_bus_undervoltage_trip = {safe_get(odrv, 'config.dc_bus_undervoltage_trip_level')} V")
    print(f"  watchdog_timeout         = {safe_get(ax, 'config.watchdog_timeout')} s")
    print(f"  dc_bus_voltage (live)    = {odrv.vbus_voltage:.2f} V")
    print()


def get_iq(odrv):
    """Get measured Iq current, trying FW 0.6.x paths."""
    return safe_get(odrv.axis0, "motor.foc.Iq_measured", "motor.current_control.Iq_measured")


def enter_closed_loop(odrv):
    """Arm the motor into closed loop control."""
    odrv.clear_errors()
    time.sleep(0.2)
    odrv.axis0.requested_state = AXIS_STATE_CLOSED_LOOP_CONTROL
    time.sleep(0.5)
    errs = check_errors(odrv)
    if errs:
        print(f"  [!] Failed to enter closed loop: {errs}")
        return False
    return True


def stop_motor(odrv):
    """Stop the motor gracefully."""
    try:
        odrv.axis0.controller.input_vel = 0
        time.sleep(1)
        odrv.axis0.requested_state = AXIS_STATE_IDLE
    except Exception:
        pass
    time.sleep(0.5)


# Config path candidates — tries each until one works with eval
CURRENT_SOFT = ("axis0.config.motor.current_soft_max", "axis0.motor.config.current_soft_max", "axis0.motor.config.current_lim")
CURRENT_HARD = ("axis0.config.motor.current_hard_max", "axis0.motor.config.current_hard_max", "axis0.motor.config.current_lim_margin")
VEL_LIMIT = ("axis0.controller.config.vel_limit",)
VEL_RAMP = ("axis0.controller.config.vel_ramp_rate",)
OV_TRIP = ("config.dc_bus_overvoltage_trip_level",)
UV_TRIP = ("config.dc_bus_undervoltage_trip_level",)


def resolve_config(odrv, candidates):
    """Find which attribute path actually exists on this ODrive."""
    for path in candidates:
        try:
            obj = odrv
            for part in path.split("."):
                obj = getattr(obj, part)
            return path, obj
        except AttributeError:
            continue
    return candidates[0], "N/A"


def run_test(odrv, name, relevant_configs, instructions, test_fn):
    """Run a single stress test."""
    print_header(name)
    print(f"\n  Relevant config(s) to adjust if this fails:")
    for candidates, direction in relevant_configs:
        path, val = resolve_config(odrv, candidates)
        print(f"    odrv0.{path} = {val}  --> {direction} if it trips")

    print(f"\n  ** {instructions} **")
    input("\n  Press ENTER when ready...")

    if not enter_closed_loop(odrv):
        return {"test": name, "result": "BLOCKED", "errors": check_errors(odrv)}

    result = test_fn(odrv)
    errors = check_errors(odrv)
    stop_motor(odrv)
    odrv.clear_errors()
    time.sleep(0.5)

    if errors:
        print(f"\n  [FAIL] {name}")
        print(f"  Errors: {errors}")
        print(f"  --> Adjust the config(s) listed above and re-run")
        return {"test": name, "result": "FAIL", "errors": errors}
    else:
        print(f"\n  [PASS] {name}")
        return {"test": name, "result": "PASS", "errors": {}}


def test_overcurrent(odrv):
    """Spin slowly - user grabs wheel to stall it."""
    print("\n  Motor spinning at vel=5. GRAB THE WHEEL to resist it for ~5 seconds.")
    print("  Monitoring current...")
    odrv.axis0.controller.input_vel = 5
    for i in range(50):  # 5 seconds
        time.sleep(0.1)
        errs = check_errors(odrv)
        if errs:
            return False
        iq = get_iq(odrv)
        if isinstance(iq, (int, float)):
            sys.stdout.write(f"\r  Iq = {iq:+.2f} A   ")
        else:
            sys.stdout.write(f"\r  Iq = {iq}   ")
        sys.stdout.flush()
    print()
    return True


def test_overspeed(odrv):
    """Ramp to high speed."""
    speeds = [2,4,8,9,10]
    for spd in speeds:
        print(f"\n  Ramping to {spd} turns/s...")
        odrv.axis0.controller.input_vel = spd
        time.sleep(2)
        errs = check_errors(odrv)
        if errs:
            print(f"  Tripped at vel={spd}")
            return False
        vel = get_vel(odrv)
        print(f"  Actual vel = {vel:.1f} turns/s")
    return True


def test_regen_hard_stop(odrv):
    """Spin up then command zero - regen spike."""
    print("\n  Spinning to vel=9...")
    odrv.axis0.controller.input_vel = 9
    wait_for_speed(odrv, 9, timeout=5)
    time.sleep(1)

    print("  HARD STOP (vel=0)...")
    odrv.axis0.controller.input_vel = 0
    for i in range(30):
        time.sleep(0.1)
        errs = check_errors(odrv)
        vbus = odrv.vbus_voltage
        sys.stdout.write(f"\r  Vbus = {vbus:.2f} V   ")
        sys.stdout.flush()
        if errs:
            print(f"\n  Vbus peaked - tripped at {vbus:.2f} V")
            return False
    print()
    return True


def test_rapid_reversal(odrv):
    """Forward then immediate reverse."""
    print("\n  Spinning to vel=9...")
    odrv.axis0.controller.input_vel = 9
    wait_for_speed(odrv, 9, timeout=5)
    time.sleep(1)

    print("  REVERSING to vel=-9...")
    odrv.axis0.controller.input_vel = -9
    for i in range(50):
        time.sleep(0.1)
        errs = check_errors(odrv)
        iq = get_iq(odrv)
        vbus = odrv.vbus_voltage
        if isinstance(iq, (int, float)):
            sys.stdout.write(f"\r  Iq = {iq:+.2f} A | Vbus = {vbus:.2f} V   ")
        else:
            sys.stdout.write(f"\r  Iq = {iq} | Vbus = {vbus:.2f} V   ")
        sys.stdout.flush()
        if errs:
            return False
    print()
    return True


def test_sustained_load(odrv):
    """Run at moderate speed for 30s, check thermal / stability."""
    print("\n  Running at vel=9 for 30 seconds...")
    print("  Watch for thermal or sustained current issues.")
    odrv.axis0.controller.input_vel = 9
    for i in range(300):
        time.sleep(0.1)
        errs = check_errors(odrv)
        if errs:
            print(f"\n  Tripped after {i/10:.1f}s")
            return False
        if i % 50 == 0:
            iq = get_iq(odrv)
            vbus = odrv.vbus_voltage
            if isinstance(iq, (int, float)):
                print(f"  t={i/10:.0f}s  Iq={iq:+.2f}A  Vbus={vbus:.2f}V")
            else:
                print(f"  t={i/10:.0f}s  Iq={iq}  Vbus={vbus:.2f}V")
    return True


def main():
    print("ODrive S1 Solo Wheel Stress Test (FW 0.6.x)")
    print("=============================================\n")
    print("Connecting to ODrive via USB...")

    odrv = odrive.find_any()
    print(f"Connected! Serial: {odrv.serial_number}")
    print(f"FW version: {odrv.fw_version_major}.{odrv.fw_version_minor}.{odrv.fw_version_revision}")
    print(f"Vbus: {odrv.vbus_voltage:.2f} V")

    print_config(odrv)

    # Check for pre-existing errors
    errs = check_errors(odrv)
    if errs:
        print(f"Pre-existing errors: {errs}")
        print("Clearing...")
        odrv.clear_errors()
        time.sleep(0.5)

    tests = [
        (
            "OVERCURRENT (hand-grab stall)",
            [
                (CURRENT_SOFT, "INCREASE"),
                (CURRENT_HARD, "INCREASE"),
            ],
            "Grab and hold the wheel firmly once it starts spinning",
            test_overcurrent,
        ),
        (
            "OVERSPEED (free spin ramp)",
            [
                (VEL_LIMIT, "INCREASE"),
            ],
            "Let the wheel spin freely, do NOT touch it",
            test_overspeed,
        ),
        (
            "REGEN OVERVOLTAGE (hard stop)",
            [
                (OV_TRIP, "INCREASE"),
                (VEL_RAMP, "DECREASE"),
            ],
            "Let the wheel spin freely, do NOT touch it",
            test_regen_hard_stop,
        ),
        (
            "RAPID REVERSAL (fwd -> rev)",
            [
                (CURRENT_SOFT, "INCREASE"),
                (OV_TRIP, "INCREASE"),
                (VEL_RAMP, "DECREASE"),
            ],
            "Let the wheel spin freely, do NOT touch it - tests current + regen combined",
            test_rapid_reversal,
        ),
        (
            "SUSTAINED LOAD (30s endurance)",
            [
                (CURRENT_SOFT, "INCREASE"),
                (UV_TRIP, "DECREASE"),
            ],
            "Let it run for 30 seconds, watch for thermal issues",
            test_sustained_load,
        ),
    ]

    results = []
    for name, configs, instructions, fn in tests:
        result = run_test(odrv, name, configs, instructions, fn)
        results.append(result)

    # Summary
    print(f"\n{'='*50}")
    print("  RESULTS SUMMARY")
    print(f"{'='*50}\n")

    for r in results:
        status = r["result"]
        icon = "PASS" if status == "PASS" else "FAIL" if status == "FAIL" else "SKIP"
        print(f"  [{icon}] {r['test']}")
        if r["errors"]:
            for src, code in r["errors"].items():
                print(f"         {src} = {code}")

    fails = [r for r in results if r["result"] != "PASS"]
    if not fails:
        print("\n  All tests passed! Your config looks solid.")
    else:
        print(f"\n  {len(fails)} test(s) failed - adjust the configs listed above,")
        print("  run odrv0.save_configuration() + odrv0.reboot(), then re-test.")

    print()


if __name__ == "__main__":
    main()
