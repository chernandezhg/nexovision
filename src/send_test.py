import base64
import json

import cv2

from src.cluster_tasks import infer_frame

camera = cv2.VideoCapture(0)

if not camera.isOpened():
    raise RuntimeError("No se pudo abrir la cámara")

frame = None

for _ in range(10):
    ok, frame = camera.read()
    if not ok:
        raise RuntimeError("No se pudo capturar el fotograma")

camera.release()

ok, encoded = cv2.imencode(
    ".jpg",
    frame,
    [cv2.IMWRITE_JPEG_QUALITY, 80],
)

if not ok:
    raise RuntimeError("No se pudo codificar el fotograma")

jpeg_base64 = base64.b64encode(encoded).decode("ascii")
task = infer_frame.delay(jpeg_base64)

print(f"Tarea enviada: {task.id}")
print("Esperando resultado; la primera carga puede tardar...")

result = task.get(timeout=120)

print(json.dumps(result, indent=2, ensure_ascii=False))
