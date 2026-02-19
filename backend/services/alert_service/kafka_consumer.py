"""
Consume inference.detections from Kafka and create alerts when threshold exceeded.
Run as separate process. Config: ALERT_THREAT_THRESHOLD, KAFKA_BOOTSTRAP_SERVERS.
"""
import os
import json
import asyncpg
from kafka import KafkaConsumer

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
DETECTIONS_TOPIC = os.getenv("INFERENCE_DETECTIONS_TOPIC", "inference.detections")
THREAT_THRESHOLD = float(os.getenv("ALERT_THREAT_THRESHOLD", "0.7"))


def run():
    consumer = KafkaConsumer(
        DETECTIONS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
        group_id="alert-detection-consumer",
        auto_offset_reset="earliest",
    )
    import asyncio
    loop = asyncio.get_event_loop()
    pool = loop.run_until_complete(asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5))
    for msg in consumer:
        try:
            payload = json.loads(msg.value.decode())
            detections = payload if isinstance(payload, list) else payload.get("detections", [payload])
            for d in detections:
                threat_score = d.get("threat_score") or d.get("confidence", 0)
                if threat_score < THREAT_THRESHOLD:
                    continue
                loop.run_until_complete(
                    pool.execute(
                        """
                        INSERT INTO alerts.alerts (source, severity, title, body, asset_id, detection_id, metadata)
                        VALUES ($1, $2, $3, $4, $5::uuid, $6, $7::jsonb)
                        """,
                        "inference",
                        "high" if threat_score >= 0.8 else "medium",
                        f"Threat detected: {d.get('class_name', 'unknown')}",
                        json.dumps(d),
                        d.get("asset_id"),
                        d.get("frame_id"),
                        json.dumps(d),
                    )
                )
        except Exception as e:
            print("alert consumer error:", e)
    pool.close()


if __name__ == "__main__":
    run()
