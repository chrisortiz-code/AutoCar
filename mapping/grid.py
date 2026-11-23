"""
Occupancy grid map using log-odds updates.

Coordinate system: world-frame, origin at robot start position.
  +X = forward, +Y = left, angles CCW from +X.
Grid cell (0,0) is at world origin (center of the grid array).
"""

import math
import numpy as np

L_FREE = -0.4
L_OCC = 0.85
L_PRIOR = 0.0
L_MIN = -5.0
L_MAX = 5.0

DEFAULT_RESOLUTION = 5.0  # cm per cell
DEFAULT_SIZE = 800         # cells per side -> 40m x 40m at 5cm


class OccupancyGrid:
    def __init__(self, resolution=DEFAULT_RESOLUTION, size=DEFAULT_SIZE):
        self.resolution = resolution  # cm per cell
        self.size = size
        self.origin_cell = size // 2  # world (0,0) maps to this cell index
        self.grid = np.full((size, size), L_PRIOR, dtype=np.float32)
        self._update_count = 0

    def world_to_cell(self, x_cm, y_cm):
        col = int(round(x_cm / self.resolution)) + self.origin_cell
        row = int(round(-y_cm / self.resolution)) + self.origin_cell
        return row, col

    def cell_to_world(self, row, col):
        x_cm = (col - self.origin_cell) * self.resolution
        y_cm = -(row - self.origin_cell) * self.resolution
        return x_cm, y_cm

    def in_bounds(self, row, col):
        return 0 <= row < self.size and 0 <= col < self.size

    def insert_scan(self, robot_x, robot_y, robot_theta, scan_points):
        """Update grid with a lidar scan.

        robot_x, robot_y: cm in world frame
        robot_theta: radians, CCW from +X
        scan_points: list of {angle: degrees, dist_mm: int}
        """
        r0, c0 = self.world_to_cell(robot_x, robot_y)
        if not self.in_bounds(r0, c0):
            return

        for pt in scan_points:
            dist_cm = pt["dist_mm"] / 10.0
            if dist_cm < 5.0 or dist_cm > 1200.0:
                continue

            angle_rad = robot_theta + math.radians(pt["angle"])
            end_x = robot_x + dist_cm * math.cos(angle_rad)
            end_y = robot_y + dist_cm * math.sin(angle_rad)
            r1, c1 = self.world_to_cell(end_x, end_y)

            cells = self._bresenham(r0, c0, r1, c1)
            for r, c in cells[:-1]:
                if self.in_bounds(r, c):
                    self.grid[r, c] = max(L_MIN, self.grid[r, c] + L_FREE)
            if self.in_bounds(r1, c1):
                self.grid[r1, c1] = min(L_MAX, self.grid[r1, c1] + L_OCC)

        self._update_count += 1

    @property
    def update_count(self):
        return self._update_count

    def probability_map(self):
        """Convert log-odds grid to probability [0, 1]."""
        return 1.0 - 1.0 / (1.0 + np.exp(self.grid))

    def to_image(self):
        """Return uint8 image: 0=occupied, 128=unknown, 255=free."""
        prob = self.probability_map()
        img = np.full_like(prob, 128, dtype=np.uint8)
        img[prob > 0.6] = 0     # occupied
        img[prob < 0.4] = 255   # free
        return img

    def to_png_bytes(self, robot_x=None, robot_y=None):
        """Render map as PNG bytes for web viewer."""
        import io
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return self._to_png_raw()

        img_arr = self.to_image()
        img = Image.fromarray(img_arr, mode='L')

        if robot_x is not None and robot_y is not None:
            r, c = self.world_to_cell(robot_x, robot_y)
            draw = ImageDraw.Draw(img)
            s = 3
            draw.ellipse([c - s, r - s, c + s, r + s], fill=100)

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()

    def _to_png_raw(self):
        """Minimal PNG without PIL — grayscale, uncompressed."""
        import io
        import struct
        import zlib

        img = self.to_image()
        h, w = img.shape
        raw = b''
        for row in img:
            raw += b'\x00' + row.tobytes()

        def chunk(ctype, data):
            c = ctype + data
            return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

        ihdr = struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0)
        compressed = zlib.compress(raw)
        png = b'\x89PNG\r\n\x1a\n'
        png += chunk(b'IHDR', ihdr)
        png += chunk(b'IDAT', compressed)
        png += chunk(b'IEND', b'')
        return png

    @staticmethod
    def _bresenham(r0, c0, r1, c1):
        """Bresenham's line from (r0,c0) to (r1,c1)."""
        cells = []
        dr = abs(r1 - r0)
        dc = abs(c1 - c0)
        sr = 1 if r1 > r0 else -1
        sc = 1 if c1 > c0 else -1
        err = dc - dr
        r, c = r0, c0
        while True:
            cells.append((r, c))
            if r == r1 and c == c1:
                break
            e2 = 2 * err
            if e2 > -dr:
                err -= dr
                c += sc
            if e2 < dc:
                err += dc
                r += sr
        return cells
