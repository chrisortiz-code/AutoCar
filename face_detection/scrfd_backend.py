"""SCRFD face detection backend via InsightFace.

pip install insightface onnxruntime-gpu

Models are downloaded automatically on first use.
Recommended for Jetson Orin Nano: buffalo_sc (uses SCRFD-500M).
"""

from .base import FaceDetector, Detection

DEFAULT_MODEL_PACK = "buffalo_sc"


class SCRFDFaceDetector(FaceDetector):
    name = "scrfd"

    def __init__(self, model_name=DEFAULT_MODEL_PACK, det_size=(640, 640),
                 ctx_id=0):
        import insightface

        self._app = insightface.app.FaceAnalysis(
            name=model_name,
            allowed_modules=["detection"],
        )
        self._app.prepare(ctx_id=ctx_id, det_size=det_size)

    def detect(self, frame_bgr):
        h, w = frame_bgr.shape[:2]
        results = self._app.get(frame_bgr)
        faces = []
        for face in results:
            x1, y1, x2, y2 = face.bbox
            bw = x2 - x1
            bh = y2 - y1
            faces.append(Detection(
                cx=(x1 + bw / 2) / w,
                cy=(y1 + bh / 2) / h,
                w=bw / w,
                h=bh / h,
                confidence=float(face.det_score),
            ))
        return faces


def create(model_name=DEFAULT_MODEL_PACK, det_size=(640, 640), ctx_id=0,
           **_kwargs):
    return SCRFDFaceDetector(model_name=model_name, det_size=det_size,
                             ctx_id=ctx_id)
