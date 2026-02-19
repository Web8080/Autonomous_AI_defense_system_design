"""
Emulate camera/LIDAR/radar feeds: generate synthetic frames or point clouds and publish to Kafka.
Consumed by inference service for testing threat detection without real sensors.
"""
import os
import json
import time
import base64
import uuid

from kafka import KafkaProducer

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
FRAMES_TOPIC = os.getenv("INFERENCE_FRAMES_TOPIC", "inference.frames")
ASSET_ID = os.getenv("SIM_ASSET_ID", str(uuid.uuid4()))
INTERVAL_SEC = float(os.getenv("SIM_FRAME_INTERVAL_SEC", "2.0"))


def make_stub_frame_b64() -> str:
    import numpy as np
    from PIL import Image
    arr = np.zeros((640, 640, 3), dtype=np.uint8)
    arr[200:400, 200:400] = [128, 0, 0]
    img = Image.fromarray(arr)
    import io
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def main():
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    frame_id = 0
    while True:
        frame_id += 1
        msg = {
            "asset_id": ASSET_ID,
            "frame_id": str(frame_id),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "image_b64": make_stub_frame_b64(),
        }
        producer.send(FRAMES_TOPIC, value=msg)
        producer.flush()
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
