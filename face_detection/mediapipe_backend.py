"""MediaPipe Face Detection backend."""

import cv2
import mediapipe as mp

from .base import FaceDetector, Detection


class MediaPipeFaceDetector(FaceDetector):
    name = "mediapipe"

    def __init__(self, model_selection=0, min_confidence=0.5):
        self._det = mp.solutions.face_detection.FaceDetection(
            model_selection=model_selection,
            min_detection_confidence=min_confidence,
        )

    def detect(self, frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        results = self._det.process(rgb)
        faces = []
        if results.detections:
            for d in results.detections:
                bb = d.location_data.relative_bounding_box
                faces.append(Detection(
                    cx=bb.xmin + bb.width / 2,
                    cy=bb.ymin + bb.height / 2,
                    w=bb.width,
                    h=bb.height,
                    confidence=d.score[0],
                ))
        return faces

    def close(self):
        self._det.close()


def create(model_selection=0, min_confidence=0.5, **_kwargs):
    return MediaPipeFaceDetector(model_selection=model_selection,
                                 min_confidence=min_confidence)
