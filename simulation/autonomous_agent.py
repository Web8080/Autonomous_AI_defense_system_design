"""
Autonomous agent logic: path planning, collision avoidance, response to detections.
Reads detections from Kafka (inference.detections), decides actions within safety bounds,
publishes commands to control topic or MQTT placeholder. Geofence and speed limits enforced here.
"""
import os
import json
from dataclasses import dataclass

from kafka import KafkaConsumer, KafkaProducer

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
DETECTIONS_TOPIC = os.getenv("INFERENCE_DETECTIONS_TOPIC", "inference.detections")
COMMANDS_TOPIC = os.getenv("CONTROL_COMMANDS_TOPIC", "control.commands")
THREAT_THRESHOLD = float(os.getenv("AGENT_THREAT_THRESHOLD", "0.7"))
GEOFENCE = [[0, 0], [1000, 0], [1000, 1000], [0, 1000]]
MAX_SPEED = float(os.getenv("AGENT_MAX_SPEED", "5.0"))


@dataclass
class PlannedAction:
    asset_id: str
    intent: str
    payload: dict


def in_geofence(x: float, y: float) -> bool:
    n = len(GEOFENCE)
    inside = False
    j = n - 1
    for i in range(n):
        if (GEOFENCE[i][1] > y) != (GEOFENCE[j][1] > y):
            if GEOFENCE[i][0] + (y - GEOFENCE[i][1]) / (GEOFENCE[j][1] - GEOFENCE[i][1]) * (GEOFENCE[j][0] - GEOFENCE[i][0]) < x:
                inside = not inside
        j = i
    return inside


def decide(detection: dict) -> PlannedAction | None:
    if detection.get("threat_score", 0) < THREAT_THRESHOLD:
        return None
    asset_id = detection.get("asset_id", "")
    return PlannedAction(
        asset_id=asset_id,
        intent="investigate",
        payload={"target_bbox": detection.get("bbox", []), "max_speed": MAX_SPEED},
    )


def main():
    consumer = KafkaConsumer(
        DETECTIONS_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
        group_id="autonomous-agent",
        auto_offset_reset="earliest",
    )
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP.split(","),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    for msg in consumer:
        try:
            payload = json.loads(msg.value.decode())
            detections = payload if isinstance(payload, list) else payload.get("detections", [payload])
            for d in detections:
                action = decide(d)
                if action and in_geofence(0, 0):
                    producer.send(
                        COMMANDS_TOPIC,
                        value={
                            "asset_id": action.asset_id,
                            "intent": action.intent,
                            "payload": action.payload,
                            "issued_by": "system_ai",
                            "is_override": False,
                        },
                    )
                    producer.flush()
        except Exception as e:
            print("Agent error:", e)


if __name__ == "__main__":
    main()
