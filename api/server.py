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
from api.routers.sensors import router as sensors_router, stream_router as sensors_stream_router
from api.routers.face import router as face_router, stream_router as face_stream_router
from api.routers.object_track import router as track_router, stream_router as track_stream_router
from api.routers.color_track import router as color_router, stream_router as color_stream_router
from api.routers.obstacles import router as obstacles_router, stream_router as obstacles_stream_router
from api.routers.viewers import router as viewers_router

app.include_router(drive_router)
app.include_router(paths_router)
app.include_router(sensors_router)
app.include_router(sensors_stream_router)
app.include_router(face_router)
app.include_router(face_stream_router)
app.include_router(track_router)
app.include_router(track_stream_router)
app.include_router(color_router)
app.include_router(color_stream_router)
app.include_router(obstacles_router)
app.include_router(obstacles_stream_router)
app.include_router(viewers_router)


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
            "track": "/api/track",
            "color": "/api/color",
            "obstacles": "/api/obstacles",
            "websocket": "/api/ws/sensors",
            "dashboard": "/viewer/dashboard",
            "viewers": "/viewer/{lidar,camera,obstacles,drive,paths}",
        },
    }


# ── Startup / shutdown ────────────────────────────────────────────────

def configure(*, demo=False, no_camera=False, no_lidar=False, cam_width=848, cam_height=480, cam_fps=30, scan_mode=0):
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
            camera = CameraReader(demo=demo, width=cam_width, height=cam_height, fps=cam_fps)
            camera.start()
            print(f"Camera: {'demo mode' if demo else 'hardware'}")
        except Exception as e:
            print(f"Camera init failed: {e}")

    if not no_lidar:
        try:
            from lidar.proxy import LidarProxy
            from lidar.reader import list_serial_ports
            if demo:
                lidar = LidarProxy("demo", demo=True, scan_mode=scan_mode)
            else:
                ports = list_serial_ports()
                port = ports[0] if len(ports) == 1 else None
                if port:
                    lidar = LidarProxy(port, scan_mode=scan_mode)
                else:
                    print(f"Lidar: no port found (available: {ports})")
            if lidar:
                lidar.start()
                print(f"Lidar: {'demo mode' if demo else lidar.port} (standalone viewer on :8092)")
        except Exception as e:
            print(f"Lidar init failed: {e}")

    set_readers(camera=camera, lidar=lidar)
    start_udp_relay()

    # Share camera with face tracker, object tracker, and obstacle detector
    if camera:
        from api.routers.face import set_camera_reader
        set_camera_reader(camera)
        from api.routers.object_track import set_camera_reader as set_track_camera
        set_track_camera(camera)
        from api.routers.color_track import set_camera_reader as set_color_camera
        set_color_camera(camera)
        from api.routers.obstacles import set_camera_reader as set_obstacles_camera
        set_obstacles_camera(camera)

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
