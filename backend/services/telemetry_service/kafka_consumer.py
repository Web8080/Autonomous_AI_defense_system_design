"""
Background worker: consume telemetry.raw from Kafka and write aggregates to DB.
Run as separate process or Celery task. Uses same DB and Kafka env vars as main app.
"""
import os
import json
import asyncio
from datetime import datetime

import asyncpg
from kafka import KafkaConsumer

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TELEMETRY_RAW_TOPIC = os.getenv("TELEMETRY_RAW_TOPIC", "telemetry.raw")


def run():
    consumer = KafkaConsumer(
        TELEMETRY_RAW_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
        group_id="telemetry-aggregator",
        auto_offset_reset="earliest",
    )
    loop = asyncio.get_event_loop()
    pool = loop.run_until_complete(asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5))
    for msg in consumer:
        try:
            payload = json.loads(msg.value.decode())
            asset_id = payload.get("asset_id")
            timestamp = payload.get("timestamp")
            source = payload.get("source", "kafka")
            if not asset_id or not timestamp:
                continue
            bucket_ts = timestamp[:16] if len(timestamp) >= 16 else timestamp
            loop.run_until_complete(
                pool.execute(
                    """
                    INSERT INTO telemetry.aggregated (asset_id, bucket_ts, source, count_events, payload_sample)
                    VALUES ($1::uuid, $2::timestamptz, $3, 1, $4::jsonb)
                    """,
                    asset_id,
                    bucket_ts,
                    source,
                    json.dumps(payload.get("payload", {})),
                )
            )
        except Exception as e:
            # Log and continue; add dead-letter or retry in production
            print("telemetry consumer error:", e)
    pool.close()


if __name__ == "__main__":
    run()
