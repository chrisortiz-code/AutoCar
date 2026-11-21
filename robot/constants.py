"""Shared physical constants for motor control and path planning.

Values are empirically tuned to produce correct real-world distances.
"""

import math

WHEEL_DIAMETER = 9.55           # cm (effective — tuned empirically)
GEAR_RATIO = 16.0 / 90.0       # motor:wheel
CM_PER_MOTOR_REV = GEAR_RATIO * math.pi * WHEEL_DIAMETER
MOTOR_REVS_PER_CM = 1.0 / CM_PER_MOTOR_REV

TURNS_PER_DEG = 0.072           # motor turns per degree of robot rotation

MOVE_TIMEOUT = 30               # seconds — safety timeout
RAMP_PCT = 0.15                 # ramp over first/last 15% of travel time
RAMP_MIN = 0.1                  # minimum ramp fraction (10% of peak)
_RAMP_AVG = 1.0 - 2 * RAMP_PCT + 2 * RAMP_PCT * (RAMP_MIN + 1.0) / 2
MOTOR_REVS_PER_CM_COMP = MOTOR_REVS_PER_CM / _RAMP_AVG
