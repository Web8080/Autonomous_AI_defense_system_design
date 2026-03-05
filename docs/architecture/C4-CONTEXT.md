# C4 Context: Autonomous AI Defense System

## Revised data flow (with ML)

```
Operators (Super Admin / Local) --> HTTPS/WSS --> API Gateway
                                                      |
Sensors (Camera, LIDAR, Radar, IoT) --> Kafka (telemetry.raw, inference.frames)
                                                      |
                                                      v
                                            Inference Service (YOLO)
                                                      |
                                                      v
                                            Kafka (inference.detections)
                                                      |
                    +---------------------------------+-----------------------------+
                    v                                 v                             v
            Alert Service                    Control Service              Dashboard (live view)
                    |                                 |
                    v                                 v
            PostgreSQL (alerts)              Autonomous Agent (bounded)
                    |                                 |
                    |                                 v
                    |                         MQTT/ROS/WebSocket --> Assets (drones, vehicles)
                    v
            Notifications / Audit
```

## C4 Context Diagram (Level 1)

**Person:** Security Operator (Super Admin, Local Operator)  
**System:** Autonomous AI Defense System  
**External:** Sensors (cameras, LIDAR, radar, IoT), Assets (drones, ground vehicles), Notifications (email, webhook)

The system receives telemetry and video frames from sensors, runs threat detection (YOLO), produces alerts and optional autonomous commands within safeguardrails, and allows full operator override and emergency stop.

## Container diagram (Level 2) – see PlantUML below

Containers: API Gateway, Asset Service, Telemetry Service, **Inference Service (ML)**, Alert Service, Control Service (with Autonomous Agent), Dashboard, Kafka, PostgreSQL, Redis, Training/MLOps (offline).

## Component diagram – Inference Service (Level 3)

- **Kafka Frame Consumer**: consumes `inference.frames`, decodes base64/image URL.
- **Model Loader**: loads YOLO (PyTorch/ONNX), quantized; warm-up on startup.
- **Detection Pipeline**: preprocess -> infer -> NMS -> map to threat classes -> cap count.
- **Kafka Producer**: publishes to `inference.detections`.
- **HTTP API**: `/infer`, `/infer/batch`, `/health`, `/metrics` (Prometheus).

See `C4-CONTAINER.puml` and `C4-COMPONENT-INFERENCE.puml` for diagrams.
