"""
path_db.py — JSON storage + FFT compression for sequential & continuous paths.
"""

import json
import os
import threading
import numpy as np

JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "paths.json")

_lock = threading.Lock()


def _read_db():
    with open(JSON_PATH, "r") as f:
        return json.load(f)


def _write_db(db):
    with open(JSON_PATH, "w") as f:
        json.dump(db, f, indent=2)


def init_db():
    if not os.path.exists(JSON_PATH):
        _write_db({"next_id": 1, "paths": {}})


# --------------- Path CRUD ---------------

def create_path(name, path_type, metadata=None):
    with _lock:
        db = _read_db()
        path_id = db["next_id"]
        db["next_id"] = path_id + 1
        from datetime import datetime
        db["paths"][str(path_id)] = {
            "id": path_id,
            "name": name,
            "path_type": path_type,
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "metadata": json.dumps(metadata) if metadata else None,
            "steps": [],
            "continuous": None,
        }
        _write_db(db)
    return path_id


def list_paths():
    db = _read_db()
    results = []
    for p in sorted(db["paths"].values(), key=lambda x: x["id"]):
        results.append({
            "id": p["id"],
            "name": p["name"],
            "path_type": p["path_type"],
            "created_at": p["created_at"],
            "metadata": p["metadata"],
        })
    return results


def get_path(path_id):
    db = _read_db()
    p = db["paths"].get(str(path_id))
    if not p:
        return None
    return {
        "id": p["id"],
        "name": p["name"],
        "path_type": p["path_type"],
        "created_at": p["created_at"],
        "metadata": p["metadata"],
    }


def delete_path(path_id):
    with _lock:
        db = _read_db()
        db["paths"].pop(str(path_id), None)
        _write_db(db)


# --------------- Sequential Steps ---------------

def set_steps(path_id, steps):
    """Replace all steps for a path. steps: list of dicts."""
    with _lock:
        db = _read_db()
        p = db["paths"].get(str(path_id))
        if not p:
            return
        p["steps"] = [
            {
                "step_order": i,
                "distance_cm": s.get("distance_cm", 0),
                "theta_deg": s.get("theta_deg", 0),
                "rotation_deg": s.get("rotation_deg", 0),
                "vel_pct": s.get("vel_pct", 30),
                "delay_ms": s.get("delay_ms", 0),
            }
            for i, s in enumerate(steps)
        ]
        _write_db(db)


def get_steps(path_id):
    db = _read_db()
    p = db["paths"].get(str(path_id))
    if not p:
        return []
    return p.get("steps", [])


# --------------- FFT Compression ---------------

def _compress_channel(samples, num_coefficients):
    """FFT compress a single channel. Returns sparse list of [index, real, imag]."""
    coeffs = np.fft.rfft(samples)
    magnitudes = np.abs(coeffs)
    n = min(num_coefficients, len(coeffs))
    top_indices = np.argsort(magnitudes)[-n:]
    sparse = []
    for idx in top_indices:
        sparse.append([int(idx), float(coeffs[idx].real), float(coeffs[idx].imag)])
    return sparse


def _decompress_channel(sparse, num_samples):
    """Reconstruct a channel from sparse FFT coefficients."""
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

        with _lock:
            db = _read_db()
            p = db["paths"].get(str(self.path_id))
            if p:
                p["continuous"] = {
                    "sample_rate": self.sample_rate,
                    "num_samples": n,
                    "duration_s": duration,
                    "num_coefficients": num_coefficients,
                    "vx_coeffs": vx_c,
                    "vy_coeffs": vy_c,
                    "trans_speed_coeffs": ts_c,
                    "rot_speed_coeffs": rs_c,
                }
                p["metadata"] = json.dumps({"duration_s": duration, "num_samples": n})
                _write_db(db)
        return {"num_samples": n, "duration_s": duration}


def reconstruct_continuous(path_id):
    """Reconstruct all 4 channels. Returns list of (vx, vy, trans_speed, rot_speed)."""
    db = _read_db()
    p = db["paths"].get(str(path_id))
    if not p or not p.get("continuous"):
        return None

    c = p["continuous"]
    n = c["num_samples"]
    vx = _decompress_channel(c["vx_coeffs"], n)
    vy = _decompress_channel(c["vy_coeffs"], n)
    ts = _decompress_channel(c["trans_speed_coeffs"], n)
    rs = _decompress_channel(c["rot_speed_coeffs"], n)
    return list(zip(vx.tolist(), vy.tolist(), ts.tolist(), rs.tolist()))


def get_continuous_info(path_id):
    """Get continuous recording metadata."""
    db = _read_db()
    p = db["paths"].get(str(path_id))
    if not p or not p.get("continuous"):
        return None
    c = p["continuous"]
    return {
        "sample_rate": c["sample_rate"],
        "num_samples": c["num_samples"],
        "duration_s": c["duration_s"],
        "num_coefficients": c["num_coefficients"],
    }


def recompress_continuous(path_id, num_coefficients):
    """Re-compress with different coefficient count. Returns reconstructed preview."""
    with _lock:
        db = _read_db()
        p = db["paths"].get(str(path_id))
        if not p or not p.get("continuous"):
            return None

        c = p["continuous"]
        n = c["num_samples"]
        vx = _decompress_channel(c["vx_coeffs"], n)
        vy = _decompress_channel(c["vy_coeffs"], n)
        ts = _decompress_channel(c["trans_speed_coeffs"], n)
        rs = _decompress_channel(c["rot_speed_coeffs"], n)

        vx_c = _compress_channel(vx, num_coefficients)
        vy_c = _compress_channel(vy, num_coefficients)
        ts_c = _compress_channel(ts, num_coefficients)
        rs_c = _compress_channel(rs, num_coefficients)

        c["num_coefficients"] = num_coefficients
        c["vx_coeffs"] = vx_c
        c["vy_coeffs"] = vy_c
        c["trans_speed_coeffs"] = ts_c
        c["rot_speed_coeffs"] = rs_c
        _write_db(db)

    vx2 = _decompress_channel(vx_c, n)
    vy2 = _decompress_channel(vy_c, n)
    ts2 = _decompress_channel(ts_c, n)
    rs2 = _decompress_channel(rs_c, n)
    return list(zip(vx2.tolist(), vy2.tolist(), ts2.tolist(), rs2.tolist()))


# Initialize DB on import
init_db()
