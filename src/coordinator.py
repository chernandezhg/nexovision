import argparse
import base64
import csv
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2

from src.cluster_tasks import infer_frame

parser = argparse.ArgumentParser()
parser.add_argument("--camera", type=int, default=0)
parser.add_argument("--imgsz", type=int, default=320)
parser.add_argument("--conf", type=float, default=0.35)
parser.add_argument("--max-pending", type=int, default=3)
parser.add_argument("--timeout", type=float, default=10)
args = parser.parse_args()

root = Path(__file__).resolve().parents[1]
output_dir = root / "resultados"
output_dir.mkdir(exist_ok=True)

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
csv_path = output_dir / f"cluster_{timestamp}.csv"

camera = cv2.VideoCapture(args.camera)

if not camera.isOpened():
    raise RuntimeError("No se pudo abrir la cámara")

camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
camera.set(cv2.CAP_PROP_FPS, 30)

pending = []
latest_frame = None
completion_times = deque(maxlen=100)

with csv_path.open("w", newline="") as csv_file:
    writer = csv.writer(csv_file)
    writer.writerow([
        "timestamp", "task_id", "worker", "roundtrip_ms",
        "worker_ms", "inference_ms", "detections"
    ])

    try:
        while True:
            ok, frame = camera.read()

            if not ok:
                break

            if len(pending) < args.max_pending:
                encoded_ok, encoded = cv2.imencode(
                    ".jpg",
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 80],
                )

                if encoded_ok:
                    jpeg_base64 = base64.b64encode(
                        encoded
                    ).decode("ascii")

                    task = infer_frame.delay(
                        jpeg_base64,
                        args.imgsz,
                        args.conf,
                    )

                    pending.append({
                        "task": task,
                        "frame": frame.copy(),
                        "sent": time.perf_counter(),
                    })

            now = time.perf_counter()

            for item in pending.copy():
                task = item["task"]

                if task.ready():
                    try:
                        result = task.get(timeout=1)
                        roundtrip_ms = (
                            time.perf_counter() - item["sent"]
                        ) * 1000

                        annotated = item["frame"]

                        for detection in result["detections"]:
                            x1, y1, x2, y2 = [
                                int(value)
                                for value in detection["xyxy"]
                            ]

                            label = (
                                f'{detection["label"]} '
                                f'{detection["confidence"]:.2f}'
                            )

                            cv2.rectangle(
                                annotated,
                                (x1, y1),
                                (x2, y2),
                                (0, 255, 0),
                                2,
                            )

                            cv2.putText(
                                annotated,
                                label,
                                (x1, max(20, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.5,
                                (0, 255, 0),
                                2,
                            )

                        completion_times.append(time.perf_counter())

                        if len(completion_times) > 1:
                            cluster_fps = (
                                len(completion_times) - 1
                            ) / (
                                completion_times[-1]
                                - completion_times[0]
                            )
                        else:
                            cluster_fps = 0

                        status = (
                            f'Worker: {result["worker"]} | '
                            f'Inferencia: {result["inference_ms"]:.1f} ms | '
                            f'RTT: {roundtrip_ms:.1f} ms | '
                            f'FPS: {cluster_fps:.1f}'
                        )

                        cv2.putText(
                            annotated,
                            status,
                            (10, 25),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.48,
                            (0, 255, 255),
                            2,
                        )

                        latest_frame = annotated

                        writer.writerow([
                            datetime.now().isoformat(),
                            result["task_id"],
                            result["worker"],
                            round(roundtrip_ms, 2),
                            result["total_worker_ms"],
                            result["inference_ms"],
                            len(result["detections"]),
                        ])
                        csv_file.flush()

                    except Exception as error:
                        print(f"Error de tarea: {error}")

                    pending.remove(item)

                elif now - item["sent"] > args.timeout:
                    print(f"Tarea vencida: {task.id}")
                    task.forget()
                    pending.remove(item)

            display = latest_frame if latest_frame is not None else frame

            cv2.putText(
                display,
                f"Pendientes: {len(pending)}",
                (10, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (255, 255, 0),
                2,
            )

            cv2.imshow("ARQ2 - Cluster de inferencia", display)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    finally:
        camera.release()
        cv2.destroyAllWindows()

print(f"Métricas guardadas en: {csv_path}")
