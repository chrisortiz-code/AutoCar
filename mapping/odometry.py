"""
Wheel odometry for mecanum drive — tracks robot pose from encoder deltas.

Pose: (x, y, theta) in cm and radians, world frame.
  +X = forward at start, +Y = left, theta CCW from +X.
"""

import math
import threading


class WheelOdometry:
    def __init__(self, cm_per_motor_rev, turns_per_deg, role_dir):
        self.cm_per_rev = cm_per_motor_rev
        self.turns_per_deg = turns_per_deg
        self.role_dir = role_dir  # {FL: -1, FR: 1, BL: -1, BR: 1}

        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0  # radians
        self._lock = threading.Lock()
        self._prev_pos = None  # {role: motor_turns}

    @property
    def pose(self):
        with self._lock:
            return self.x, self.y, self.theta

    def reset(self):
        with self._lock:
            self.x = 0.0
            self.y = 0.0
            self.theta = 0.0
            self._prev_pos = None

    def update(self, positions):
        """Update pose from motor positions dict {FL: turns, FR: turns, ...}.

        Uses mecanum inverse kinematics to estimate body-frame velocities
        from wheel encoder deltas, then integrates into world frame.
        """
        with self._lock:
            if self._prev_pos is None:
                self._prev_pos = dict(positions)
                return

            deltas = {}
            for role in ["FL", "FR", "BL", "BR"]:
                prev = self._prev_pos.get(role, 0.0)
                curr = positions.get(role, 0.0)
                deltas[role] = (curr - prev) * self.role_dir[role]

            self._prev_pos = dict(positions)

            fl = deltas.get("FL", 0.0)
            fr = deltas.get("FR", 0.0)
            bl = deltas.get("BL", 0.0)
            br = deltas.get("BR", 0.0)

            # Mecanum FK: wheel deltas -> body displacement
            # vx  = (FL + FR + BL + BR) / 4
            # vy  = (-FL + FR + BL - BR) / 4
            # omega = (-FL + FR - BL + BR) / 4
            d_fwd = (fl + fr + bl + br) / 4.0 * self.cm_per_rev
            d_lat = (-fl + fr + bl - br) / 4.0 * self.cm_per_rev
            d_theta = (-fl + fr - bl + br) / 4.0 / self.turns_per_deg
            d_theta_rad = math.radians(d_theta)

            # Integrate in world frame
            cos_t = math.cos(self.theta + d_theta_rad / 2.0)
            sin_t = math.sin(self.theta + d_theta_rad / 2.0)
            self.x += d_fwd * cos_t - d_lat * sin_t
            self.y += d_fwd * sin_t + d_lat * cos_t
            self.theta += d_theta_rad
