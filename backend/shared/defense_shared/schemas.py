from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AssetType(str, Enum):
    DRONE = "drone"
    GROUND_VEHICLE = "ground_vehicle"
    SENSOR = "sensor"
    CAMERA = "camera"
    LIDAR = "lidar"
    RADAR = "radar"
    IOT = "iot"


class AssetStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    IN_MISSION = "in_mission"
    ERROR = "error"
    MAINTENANCE = "maintenance"


class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    LOCAL_OPERATOR = "local_operator"
    SYSTEM_AI = "system_ai"


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class AlertState(str, Enum):
    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


class AssetCreate(BaseModel):
    name: str
    asset_type: AssetType
    region_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class AssetResponse(BaseModel):
    id: str
    name: str
    asset_type: AssetType
    region_id: str
    status: AssetStatus
    metadata: dict[str, Any]
    tags: list[str]
    created_at: str
    updated_at: str


class TelemetryPoint(BaseModel):
    asset_id: str
    timestamp: str
    source: str
    payload: dict[str, Any]


class Detection(BaseModel):
    asset_id: str
    frame_id: str
    timestamp: str
    class_name: str
    confidence: float
    threat_score: float
    bbox: list[float]
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertCreate(BaseModel):
    source: str
    severity: AlertSeverity
    title: str
    body: str
    asset_id: str | None = None
    region_id: str | None = None
    detection_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommandIntent(str, Enum):
    EMERGENCY_STOP = "emergency_stop"
    OVERRIDE = "override"
    PATH_PLAN = "path_plan"
    MISSION_ABORT = "mission_abort"
    TAKE_CONTROL = "take_control"


class CommandRequest(BaseModel):
    asset_id: str
    intent: CommandIntent
    payload: dict[str, Any] = Field(default_factory=dict)
    issued_by: str
    is_override: bool = False
