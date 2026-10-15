import argparse
import csv
import socket
import time
from datetime import datetime
from pathlib import Path

import cv2
import psutil
from ultralytics import YOLO

parser = argparse.ArgumentParser()
parser.add_argument("--model", default="yolo11n.pt")
parser.add_argument("--camera", type=int, default=0)
parser.add_argument("--imgsz", type=int, default=320)
parser.add_argument("--conf", type=float, default=0.35)
args = parser.parse_args()

root = Path(__file__).resolve().parents[1]
output_dir = root / "resultados"
output_dir.mkdir(exist_ok=True)

host = socket.gethostname()
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
csv_path = output_dir / f"local_{host}_{timestamp}.csv"

model = YOLO(args.model)
camera = cv2.VideoCapture(args.camera)

if not camera.isOpened():
    raise RuntimeError("No se pudo abrir la cámara")

camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
camera.set(cv2.CAP_PROP_FPS, 30)

psutil.cpu_percent()
frame_number = 0

with csv_path.open("w", newline="") as csv_file:
    writer = csv.writer(csv_file)
    writer.writerow([
        "frame", "host", "modelo", "total_ms", "inferencia_ms",
        "fps", "cpu_percent"
    ])

    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                break

            start = time.perf_counter()
            result = model.predict(
                frame,
                imgsz=args.imgsz,
                conf=args.conf,
                device="cpu",
                verbose=False,
            )[0]
            total_ms = (time.perf_counter() - start) * 1000
            inference_ms = result.speed["inference"]
            fps = 1000 / total_ms
            cpu = psutil.cpu_percent()

            annotated = result.plot()
            label = (
                f"{host} | FPS {fps:.1f} | "
                f"Inferencia {inference_ms:.1f} ms | CPU {cpu:.0f}%"
            )
            cv2.putText(
                annotated, label, (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2
            )

            writer.writerow([
                frame_number, host, args.model, round(total_ms, 2),
                round(inference_ms, 2), round(fps, 2), cpu
            ])

            if frame_number % 30 == 0:
                csv_file.flush()

            cv2.imshow("ARQ2 - Inferencia local", annotated)
            frame_number += 1

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        camera.release()
        cv2.destroyAllWindows()

print(f"Métricas guardadas en: {csv_path}")

