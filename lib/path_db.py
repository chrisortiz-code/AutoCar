"""
path_db.py — SQLite storage + FFT compression for sequential & continuous paths.
"""

import json
import sqlite3
import os
import numpy as np

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "paths.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS paths (
            id          INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            path_type   TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now')),
            metadata    TEXT
        );

        CREATE TABLE IF NOT EXISTS path_steps (
            id           INTEGER PRIMARY KEY,
            path_id      INTEGER REFERENCES paths(id) ON DELETE CASCADE,
            step_order   INTEGER NOT NULL,
            distance_cm  REAL NOT NULL DEFAULT 0,
            theta_deg    REAL NOT NULL DEFAULT 0,
            rotation_deg REAL NOT NULL DEFAULT 0,
            vel_pct      REAL NOT NULL DEFAULT 30,
            delay_ms     INTEGER NOT NULL DEFAULT 0,
            UNIQUE(path_id, step_order)
        );

        CREATE TABLE IF NOT EXISTS path_continuous (
            id                 INTEGER PRIMARY KEY,
            path_id            INTEGER REFERENCES paths(id) ON DELETE CASCADE,
            sample_rate        REAL NOT NULL,
            num_samples        INTEGER NOT NULL,
            duration_s         REAL NOT NULL,
            num_coefficients   INTEGER NOT NULL,
            vx_coeffs          BLOB NOT NULL,
            vy_coeffs          BLOB NOT NULL,
            trans_speed_coeffs BLOB NOT NULL,
            rot_speed_coeffs   BLOB NOT NULL
        );
    """)
    conn.commit()
    conn.close()


# --------------- Path CRUD ---------------

def create_path(name, path_type, metadata=None):
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO paths (name, path_type, metadata) VALUES (?, ?, ?)",
        (name, path_type, json.dumps(metadata) if metadata else None),
    )
    path_id = cur.lastrowid
    conn.commit()
    conn.close()
    return path_id


def list_paths():
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, name, path_type, created_at, metadata FROM paths ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_path(path_id):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM paths WHERE id = ?", (path_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_path(path_id):
    conn = _get_conn()
    conn.execute("DELETE FROM paths WHERE id = ?", (path_id,))
    conn.commit()
    conn.close()


# --------------- Sequential Steps ---------------

def set_steps(path_id, steps):
    """Replace all steps for a path. steps: list of dicts."""
    conn = _get_conn()
    conn.execute("DELETE FROM path_steps WHERE path_id = ?", (path_id,))
    for i, s in enumerate(steps):
        conn.execute(
            """INSERT INTO path_steps
               (path_id, step_order, distance_cm, theta_deg, rotation_deg, vel_pct, delay_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                path_id, i,
                s.get("distance_cm", 0),
                s.get("theta_deg", 0),
                s.get("rotation_deg", 0),
                s.get("vel_pct", 30),
                s.get("delay_ms", 0),
            ),
        )
    conn.commit()
    conn.close()


def get_steps(path_id):
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM path_steps WHERE path_id = ? ORDER BY step_order",
        (path_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------- FFT Compression ---------------

def _compress_channel(samples, num_coefficients):
    """FFT compress a single channel. Returns sparse JSON blob."""
    coeffs = np.fft.rfft(samples)
    magnitudes = np.abs(coeffs)
    # Keep top-N by magnitude
    n = min(num_coefficients, len(coeffs))
    top_indices = np.argsort(magnitudes)[-n:]
    sparse = []
    for idx in top_indices:
        sparse.append((int(idx), float(coeffs[idx].real), float(coeffs[idx].imag)))
    return json.dumps(sparse).encode("utf-8")


def _decompress_channel(blob, num_samples):
    """Reconstruct a channel from sparse FFT coefficients."""
    sparse = json.loads(blob)
    n_coeffs = num_samples // 2 + 1
    coeffs = np.zeros(n_coeffs, dtype=complex)
    for idx, real, imag in sparse:
        if idx < n_coeffs:
            coeffs[idx] = complex(real, imag)
    return np.fft.irfft(coeffs, n=num_samples)


class ContinuousRecorder:
    """Buffers raw samples, then FFT-compresses and stores on finish."""

    def __init__(self, path_id, sample_rate=20):
        self.path_id = path_id
        self.sample_rate = sample_rate
        self.vx_buf = []
        self.vy_buf = []
        self.trans_buf = []
        self.rot_buf = []

    def add_sample(self, vx, vy, trans_speed, rot_speed):
        self.vx_buf.append(vx)
        self.vy_buf.append(vy)
        self.trans_buf.append(trans_speed)
        self.rot_buf.append(rot_speed)

    @property
    def num_samples(self):
        return len(self.vx_buf)

    def finish(self, num_coefficients=50):
        n = self.num_samples
        if n < 2:
            raise ValueError("Need at least 2 samples to compress")

        duration = n / self.sample_rate
        vx_c = _compress_channel(np.array(self.vx_buf), num_coefficients)
        vy_c = _compress_channel(np.array(self.vy_buf), num_coefficients)
        ts_c = _compress_channel(np.array(self.trans_buf), num_coefficients)
        rs_c = _compress_channel(np.array(self.rot_buf), num_coefficients)

        conn = _get_conn()
        conn.execute(
            """INSERT INTO path_continuous
               (path_id, sample_rate, num_samples, duration_s, num_coefficients,
                vx_coeffs, vy_coeffs, trans_speed_coeffs, rot_speed_coeffs)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (self.path_id, self.sample_rate, n, duration, num_coefficients,
             vx_c, vy_c, ts_c, rs_c),
        )
        # Update path metadata with duration
        conn.execute(
            "UPDATE paths SET metadata = ? WHERE id = ?",
            (json.dumps({"duration_s": duration, "num_samples": n}), self.path_id),
        )
        conn.commit()
        conn.close()
        return {"num_samples": n, "duration_s": duration}


def reconstruct_continuous(path_id):
    """Reconstruct all 4 channels. Returns list of (vx, vy, trans_speed, rot_speed)."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM path_continuous WHERE path_id = ?", (path_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None

    n = row["num_samples"]
    vx = _decompress_channel(row["vx_coeffs"], n)
    vy = _decompress_channel(row["vy_coeffs"], n)
    ts = _decompress_channel(row["trans_speed_coeffs"], n)
    rs = _decompress_channel(row["rot_speed_coeffs"], n)
    return list(zip(vx.tolist(), vy.tolist(), ts.tolist(), rs.tolist()))


def get_continuous_info(path_id):
    """Get continuous recording metadata."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT sample_rate, num_samples, duration_s, num_coefficients FROM path_continuous WHERE path_id = ?",
        (path_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def recompress_continuous(path_id, num_coefficients):
    """Re-compress with different coefficient count. Returns reconstructed preview."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM path_continuous WHERE path_id = ?", (path_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None

    n = row["num_samples"]
    # Decompress with original coefficients
    vx = _decompress_channel(row["vx_coeffs"], n)
    vy = _decompress_channel(row["vy_coeffs"], n)
    ts = _decompress_channel(row["trans_speed_coeffs"], n)
    rs = _decompress_channel(row["rot_speed_coeffs"], n)

    # Re-compress with new coefficient count
    vx_c = _compress_channel(vx, num_coefficients)
    vy_c = _compress_channel(vy, num_coefficients)
    ts_c = _compress_channel(ts, num_coefficients)
    rs_c = _compress_channel(rs, num_coefficients)

    conn.execute(
        """UPDATE path_continuous
           SET num_coefficients = ?, vx_coeffs = ?, vy_coeffs = ?,
               trans_speed_coeffs = ?, rot_speed_coeffs = ?
           WHERE path_id = ?""",
        (num_coefficients, vx_c, vy_c, ts_c, rs_c, path_id),
    )
    conn.commit()
    conn.close()

    # Return preview of reconstructed signals
    vx2 = _decompress_channel(vx_c, n)
    vy2 = _decompress_channel(vy_c, n)
    ts2 = _decompress_channel(ts_c, n)
    rs2 = _decompress_channel(rs_c, n)
    return list(zip(vx2.tolist(), vy2.tolist(), ts2.tolist(), rs2.tolist()))


# Initialize DB on import
init_db()
