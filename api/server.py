"""
Unified API server — single FastAPI app consolidating all endpoints on port 8080.

Replaces the 4 independent Flask servers (5000, 5001, 8090, 8092) with one API
that any client (web, Android, CLI) can talk to. The existing UDP protocol to
universal_receiver.py on the Jetson stays unchanged.

Usage:
    python -m api                           # full hardware mode
    python -m api --demo                    # mock sensors, no hardware
    python -m api --no-camera --no-lidar    # motors only
    python -m api --port 9000               # custom port
"""

import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ── App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="AutoCar API",
    description="Unified control API for the AutoCar robot platform",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────

from api.routers.drive import router as drive_router
from api.routers.paths import router as paths_router
from api.routers.sensors import router as sensors_router, ws_router as sensors_ws_router
from api.routers.face import router as face_router

app.include_router(drive_router)
app.include_router(paths_router)
app.include_router(sensors_router)
app.include_router(sensors_ws_router)
app.include_router(face_router)


@app.get("/")
def root():
    return {
        "name": "AutoCar API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "drive": "/api/drive",
            "paths": "/api/paths",
            "sensors": "/api/sensors",
            "face": "/api/face",
            "websocket": "/api/ws/sensors",
        },
    }


# ── Startup / shutdown ────────────────────────────────────────────────

def configure(*, demo=False, no_camera=False, no_lidar=False):
    """Configure sensor readers before server starts.

    Called from __main__.py with CLI flags.
    """
    from api.routers.sensors import set_readers
    from api.routers.paths import start_udp_relay

    camera = None
    lidar = None

    # Add project root to path for imports
    project_root = os.path.join(os.path.dirname(__file__), "..")
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    if not no_camera:
        try:
            from camera.reader import CameraReader
            camera = CameraReader(demo=demo)
            camera.start()
            print(f"Camera: {'demo mode' if demo else 'hardware'}")
        except Exception as e:
            print(f"Camera init failed: {e}")

    if not no_lidar:
        try:
            from lidar.reader import LidarReader, list_serial_ports
            if demo:
                lidar = LidarReader("demo", demo=True)
            else:
                ports = list_serial_ports()
                port = ports[0] if len(ports) == 1 else None
                if port:
                    lidar = LidarReader(port)
                else:
                    print(f"Lidar: no port found (available: {ports})")
            if lidar:
                lidar.start()
                print(f"Lidar: {'demo mode' if demo else lidar.port}")
        except Exception as e:
            print(f"Lidar init failed: {e}")

    set_readers(camera=camera, lidar=lidar)
    start_udp_relay()

    # Store refs for cleanup
    app.state.camera = camera
    app.state.lidar = lidar


def _cleanup():
    """Stop hardware readers — called on shutdown and atexit."""
    if hasattr(app.state, "camera") and app.state.camera:
        app.state.camera.stop()
        app.state.camera = None
    if hasattr(app.state, "lidar") and app.state.lidar:
        app.state.lidar.stop()
        app.state.lidar = None


@app.on_event("shutdown")
def shutdown():
    _cleanup()


# atexit ensures motor stops even if uvicorn doesn't fire shutdown event
import atexit
atexit.register(_cleanup)
