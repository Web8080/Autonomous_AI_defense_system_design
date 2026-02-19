"""
MQTT adapter placeholder: publish commands to broker for real drones/vehicles.
Set MQTT_BROKER_URL and credentials in env. Do not connect to hardware without approval.
"""
import os

MQTT_BROKER_URL = os.getenv("MQTT_BROKER_URL", "")
MQTT_TOPIC_COMMANDS = os.getenv("MQTT_TOPIC_COMMANDS", "defense/commands")


def send_command(asset_id: str, intent: str, payload: dict) -> bool:
    if not MQTT_BROKER_URL or "PLACEHOLDER" in MQTT_BROKER_URL:
        return False
    try:
        import paho.mqtt.client as mqtt
        import json
        client = mqtt.Client()
        client.connect(MQTT_BROKER_URL.replace("mqtt://", "").split(":")[0], 1883, 60)
        client.publish(
            f"{MQTT_TOPIC_COMMANDS}/{asset_id}",
            json.dumps({"intent": intent, "payload": payload}),
        )
        client.disconnect()
        return True
    except Exception:
        return False
