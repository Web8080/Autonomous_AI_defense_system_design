"""
Telemetry subscriber: streams vehicle state from the drone bridge to Kafka.

Each connected aircraft gets a task that publishes VehicleState snapshots at a
configurable rate. Downstream (telemetry_service) persists to TimescaleDB.

We publish through the existing kafka-python producer. If Kafka is not reachable
the subscriber degrades gracefully (logs, keeps flying) rather than blocking the
control plane — telemetry loss must never interrupt a flight.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from kafka import KafkaProducer

from .connection import VehicleLink

log = logging.getLogger("drone_bridge.telemetry")

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TELEMETRY_RAW_TOPIC = os.getenv("TELEMETRY_RAW_TOPIC", "telemetry.raw")
TELEMETRY_PUBLISH_HZ = float(os.getenv("TELEMETRY_PUBLISH_HZ", "2.0"))


class TelemetryPublisher:
    """Publishes VehicleState snapshots to Kafka at a fixed rate."""

    def __init__(self) -> None:
        self._producer: KafkaProducer | None = None
        self._failed = False

    def _get_producer(self) -> KafkaProducer | None:
        if self._producer is None and not self._failed:
            try:
                self._producer = KafkaProducer(
                    bootstrap_servers=KAFKA_BOOTSTRAP,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    acks=1,
                    request_timeout_ms=3000,
                )
            except Exception as exc:
                self._failed = True
                log.warning("kafka producer unavailable: %s", exc)
        return self._producer

    def publish(self, state: dict[str, Any]) -> None:
        producer = self._get_producer()
        if producer is None:
            return
        try:
            producer.send(TELEMETRY_RAW_TOPIC, value=state)
        except Exception as exc:
            log.warning("telemetry publish failed: %s", exc)

    async def close(self) -> None:
        if self._producer:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._producer.flush)
            await loop.run_in_executor(None, self._producer.close)
            self._producer = None


class TelemetrySubscriber:
    """One background task per vehicle publishing state at a fixed rate."""

    def __init__(self, publisher: TelemetryPublisher, publish_hz: float = TELEMETRY_PUBLISH_HZ):
        self._publisher = publisher
        self._publish_hz = publish_hz
        self._tasks: dict[str, asyncio.Task] = {}

    async def _run_for(self, link: VehicleLink) -> None:
        interval = 1.0 / max(self._publish_hz, 0.5)
        while True:
            state = link.state.to_dict()
            self._publisher.publish(state)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return

    def start(self, link: VehicleLink) -> None:
        existing = self._tasks.get(link.asset_id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(self._run_for(link))
        self._tasks[link.asset_id] = task
        link.add_task(task)

    def stop(self, asset_id: str) -> None:
        task = self._tasks.pop(asset_id, None)
        if task:
            task.cancel()

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        await self._publisher.close()