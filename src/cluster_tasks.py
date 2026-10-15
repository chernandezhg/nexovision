import base64
import os
import socket
import time
from pathlib import Path

import cv2
import numpy as np
from celery import Celery
from ultralytics import YOLO

PROJECT_ROOT = Path(__file__).resolve().parents[1]

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://127.0.0.1:6379/0",
)

MODEL_PATH = os.getenv(
    "MODEL_PATH",
    str(PROJECT_ROOT / "yolo11n_openvino_model"),
)

app = Celery(
    "arq2",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=300,
)

_model = None


def get_model():
    global _model

    if _model is None:
        print(f"Cargando modelo: {MODEL_PATH}")
        _model = YOLO(MODEL_PATH)

    return _model


@app.task(
    bind=True,
    name="arq2.infer_frame",
    acks_late=True,
)
def infer_frame(self, jpeg_base64, imgsz=320, confidence=0.35):
    task_start = time.perf_counter()

    jpeg_bytes = base64.b64decode(jpeg_base64)
    encoded = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)

    if frame is None:
        raise ValueError("No se pudo decodificar el fotograma")

    result = get_model().predict(
        frame,
        imgsz=imgsz,
        conf=confidence,
        device="cpu",
        verbose=False,
    )[0]

    detections = []

    for box in result.boxes:
        class_id = int(box.cls[0].item())
        confidence_value = float(box.conf[0].item())
        coordinates = [
            round(float(value), 2)
            for value in box.xyxy[0].tolist()
        ]

        detections.append({
            "class_id": class_id,
            "label": result.names[class_id],
            "confidence": round(confidence_value, 4),
            "xyxy": coordinates,
        })

    return {
        "task_id": self.request.id,
        "worker": socket.gethostname(),
        "model": Path(MODEL_PATH).name,
        "inference_ms": round(result.speed["inference"], 2),
        "total_worker_ms": round(
            (time.perf_counter() - task_start) * 1000,
            2,
        ),
        "detections": detections,
    }
