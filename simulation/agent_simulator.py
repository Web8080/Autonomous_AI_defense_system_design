"""
Simulate drone and ground vehicle dynamics. Publish telemetry to Kafka (telemetry.raw)
and optionally consume commands from a control topic for closed-loop testing.
No real hardware; safe for local runs.
"""
import os
import json
import time
import uuid
from dataclasses import dataclass, asdict

from kafka import KafkaProducer

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TELEMETRY_TOPIC = os.getenv("TELEMETRY_RAW_TOPIC", "telemetry.raw")
ASSET_ID = os.getenv("SIM_ASSET_ID", str(uuid.uuid4()))
REGION_ID = os.getenv("SIM_REGION_ID", "region-1")
INTERVAL_SEC = float(os.getenv("SIM_INTERVAL_SEC", "1.0"))


@dataclass
class AgentState:
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    heading: float
    status: str


def step(state: AgentState, dt: float) -> AgentState:
    state.x += state.vx * dt
    state.y += state.vy * dt
    state.z += state.vz * dt
    return state


def main():
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    state = AgentState(0.0, 0.0, 10.0, 0.5, 0.0, 0.0, 0.0, "in_mission")
    while True:
        state = step(state, INTERVAL_SEC)
        msg = {
            "asset_id": ASSET_ID,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "simulator",
            "payload": {
                "region_id": REGION_ID,
                "position": [state.x, state.y, state.z],
                "velocity": [state.vx, state.vy, state.vz],
                "heading": state.heading,
                "status": state.status,
            },
        }
        producer.send(TELEMETRY_TOPIC, value=msg)
        producer.flush()
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
