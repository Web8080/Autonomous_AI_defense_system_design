"""Transport for simulation exercises.

Frames go out on the SAME `inference.frames` topic the product's camera feed
uses, and detections come back on the SAME `inference.detections` topic the
alert/detection services consume. A simulation exercise therefore exercises the
real, trained-model product path — the only difference is where the pixels came
from (saved footage / composer / browser 3D).

kafka-python is synchronous, so the pump runs in a worker thread and the
detection callback comes from a dedicated consumer thread. Tests inject a fake
transport; the engine logic is transport-independent.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Optional

from kafka import KafkaConsumer, KafkaProducer  # type: ignore

FRAMES_TOPIC = "inference.frames"
DETECTIONS_TOPIC = "inference.detections"

OnDetection = Callable[[dict], None]


class Transport:
    def send_frame(self, msg: dict) -> None:  # pragma: no cover - protocol
        raise NotImplementedError

    def start(self, on_detection: OnDetection) -> None:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        raise NotImplementedError


class KafkaTransport(Transport):
    def __init__(self, bootstrap_servers: str) -> None:
        self.bootstrap_servers = bootstrap_servers.split(",") if "," in bootstrap_servers else [bootstrap_servers]
        self._producer: Optional[KafkaProducer] = None
        self._consumer: Optional[KafkaConsumer] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def send_frame(self, msg: dict) -> None:
        if self._producer is None:
            self._producer = KafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )
        try:
            self._producer.send(FRAMES_TOPIC, value=msg)
            self._producer.flush()
        except Exception as exc:  # noqa: BLE001
            # A slow/broken Kafka must not kill an exercise mid-flight; the
            # status endpoint will surface it as last_error.
            raise RuntimeError(f"frame publish failed: {exc}") from exc

    def start(self, on_detection: OnDetection) -> None:
        """Consume `inference.detections` in a background thread."""
        self._consumer = KafkaConsumer(
            DETECTIONS_TOPIC,
            bootstrap_servers=self.bootstrap_servers,
            auto_offset_reset="latest",
            enable_auto_commit=True,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )
        self._thread = threading.Thread(
            target=self._consume_loop, args=(on_detection,), daemon=True
        )
        self._thread.start()

    def _consume_loop(self, on_detection: OnDetection) -> None:
        while not self._stop.is_set():
            try:
                for msg in self._consumer:  # type: ignore[union-attr]
                    if self._stop.is_set():
                        break
                    try:
                        on_detection(msg.value)
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                time.sleep(1.0)

    def close(self) -> None:
        self._stop.set()
        try:
            if self._consumer:
                self._consumer.close()
        except Exception:  # noqa: BLE001
            pass
        if self._thread:
            self._thread.join(timeout=3)


class FakeTransport(Transport):
    """In-memory transport for tests: frames land here, detections are pushed back."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._on_detection: Optional[OnDetection] = None

    def send_frame(self, msg: dict) -> None:
        self.sent.append(msg)

    def start(self, on_detection: OnDetection) -> None:
        self._on_detection = on_detection

    def push_detection(self, msg: dict) -> None:
        if self._on_detection:
            self._on_detection(msg)

    def close(self) -> None:
        pass