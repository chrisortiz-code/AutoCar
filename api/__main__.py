"""
Entry point: python -m api

Starts the unified FastAPI server on port 8080.
"""

import argparse
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


def main():
    parser = argparse.ArgumentParser(description="AutoCar unified API server")
    parser.add_argument("--port", type=int, default=int(os.getenv("API_PORT", "8080")),
                        help="Server port (default: 8080)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--demo", action="store_true",
                        help="Use mock sensor data (no hardware required)")
    parser.add_argument("--no-camera", action="store_true",
                        help="Disable camera reader")
    parser.add_argument("--no-lidar", action="store_true",
                        help="Disable lidar reader")
    parser.add_argument("--cam-width", type=int, default=424, help="Camera width")
    parser.add_argument("--cam-height", type=int, default=240, help="Camera height")
    parser.add_argument("--cam-fps", type=int, default=15, help="Camera FPS")
    parser.add_argument("--reload", action="store_true",
                        help="Enable auto-reload for development")
    args = parser.parse_args()

    from api.server import app, configure

    configure(demo=args.demo, no_camera=args.no_camera, no_lidar=args.no_lidar,
              cam_width=args.cam_width, cam_height=args.cam_height, cam_fps=args.cam_fps)

    from api.udp import UDP_HOST, UDP_PORT
    print(f"\nAutoCar API server starting on http://{args.host}:{args.port}")
    print(f"  Docs:   http://localhost:{args.port}/docs")
    print(f"  UDP ->  {UDP_HOST}:{UDP_PORT}")
    if args.demo:
        print("  Mode:   DEMO (mock sensors)")
    print()

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
