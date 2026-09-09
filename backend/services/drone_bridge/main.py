"""
Drone Bridge Service — MAVLink/MAVSDK control plane for aircraft.

Responsibilities:
  - Maintain one MAVLink connection per registered vehicle (LinkRegistry).
  - Expose vehicle state (live telemetry) over REST.
  - Execute missions (upload, arm, run, monitor, RTL on abort).
  - Provide emergency-stop and RTL commands as first-class endpoints.
  - Publish telemetry to Kafka so the rest of the platform can consume it.

Safety model: pre-arm gates and in-flight guards live in safety.py; the flight
controller also gets limits pushed via PX4 params (apply_fc_safety_params) so
they hold even if this service exits mid-flight.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from defense_shared.safeguardrails import validate_asset_id

from .connection import VehicleLink, LinkRegistry, LinkState
from .mavlink_bridge import MAVLinkBridge, BridgeRegistry
from .mission import MissionExecutor, MissionPlan, MissionStatus
from .telemetry_publisher import TelemetryPublisher, TelemetrySubscriber
from .safety import SafetyEnvelope

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("drone_bridge")

# Map of asset UUID -> connection config. In production this comes from the
# asset service; for Phase 1 it is injected via DRONE_ASSETS (JSON).
DRONE_ASSETS_JSON = os.getenv("DRONE_ASSETS", "{}")
DRONE_ASSETS: dict[str, dict[str, Any]] = json.loads(DRONE_ASSETS_JSON)


class ConnectRequest(BaseModel):
    address: str
    mavsdk_port: int | None = None


class ArmRequest(BaseModel):
    approved: bool = False
    approved_by: str | None = None


class MissionRequest(BaseModel):
    mission_id: str | None = None
    flight_id: str | None = None
    approved: bool = False
    approved_by: str | None = None
    waypoints: list[dict[str, Any]] = Field(default_factory=list)
    envelope: dict[str, Any] = Field(default_factory=dict)


link_registry: LinkRegistry = LinkRegistry()
bridge_registry: BridgeRegistry = BridgeRegistry(link_registry)
publisher: TelemetryPublisher = TelemetryPublisher()
telemetry_subscriber: TelemetrySubscriber = TelemetrySubscriber(publisher)
_executors: dict[str, MissionExecutor] = {}
_status_client: httpx.AsyncClient | None = None
MISSION_SERVICE_URL = os.getenv("MISSION_SERVICE_URL", "")


async def _start_vehicle(asset_id: str, cfg: dict[str, Any]) -> None:
    link = VehicleLink(
        asset_id=asset_id,
        system_address=cfg["address"],
        mavsdk_port=cfg.get("mavsdk_port"),
    )
    await link_registry.add(link)
    connect_task = asyncio.create_task(link.connect())
    link.add_task(connect_task)
    log.info("vehicle configured asset=%s addr=%s", asset_id, cfg["address"])


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _status_client
    if MISSION_SERVICE_URL:
        _status_client = httpx.AsyncClient(base_url=MISSION_SERVICE_URL, timeout=5.0)
    for asset_id, cfg in DRONE_ASSETS.items():
        if not validate_asset_id(asset_id):
            log.error("invalid DRONE_ASSETS key: %r", asset_id)
            continue
        await _start_vehicle(asset_id, cfg)
    yield
    if _status_client:
        await _status_client.aclose()
    await telemetry_subscriber.close()
    await link_registry.close_all()


_FS = {"executing": "in_flight", "rtl": "returning", "landed": "completed",
       "completed": "completed", "aborted": "aborted", "failed": "failed",
       "arming": "armed", "uploaded": "armed", "uploading": "armed", "pending": "armed"}


async def _notify_mission_status(flight_id: str | None, status: str, payload: dict) -> None:
    """Fire-and-forget status report to the mission service, if configured."""
    if not flight_id or _status_client is None:
        return
    fs = _FS.get(status)
    if fs is None:
        return
    try:
        await _status_client.post(
            f"/flights/{flight_id}/status",
            json={"status": fs, "abort_reason": payload.get("reason") and str(payload["reason"])},
        )
    except Exception as exc:  # noqa: BLE001 - status reporting must not break flying
        log.warning("failed to report flight status flight=%s status=%s err=%s",
                    flight_id, status, exc)


def _make_status_callback():
    def cb(status: str, payload: dict) -> None:
        asyncio.get_event_loop().create_task(
            _notify_mission_status(payload.get("flight_id"), status, payload))
    return cb


app = FastAPI(title="Drone Bridge Service", lifespan=lifespan)


def _link_or_404(asset_id: str) -> VehicleLink:
    if not validate_asset_id(asset_id):
        raise HTTPException(status_code=400, detail="Invalid asset_id")
    link = link_registry.get(asset_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Vehicle not configured")
    return link


async def _bridge_or_404(asset_id: str) -> MAVLinkBridge:
    link = _link_or_404(asset_id)
    if link.link_state in (LinkState.DISCONNECTED, LinkState.FAILED):
        raise HTTPException(status_code=409, detail="Vehicle not connected")
    bridge = bridge_registry.get(asset_id) or await bridge_registry.create(asset_id)
    if bridge is None:
        raise HTTPException(status_code=409, detail="Vehicle bridge unavailable")
    return bridge


@app.get("/health")
async def health() -> dict:
    connected = sum(1 for l in link_registry.all() if l.link_state == LinkState.CONNECTED)
    return {
        "status": "ok",
        "service": "drone-bridge",
        "vehicles_configured": len(DRONE_ASSETS),
        "vehicles_connected": connected,
    }


@app.get("/vehicles")
async def list_vehicles() -> dict:
    items = []
    for link in link_registry.all():
        bridge = bridge_registry.get(link.asset_id)
        items.append({
            "asset_id": link.asset_id,
            "link_state": link.link_state.value,
            "state": link.state.to_dict(),
            "mission_status": _executors.get(link.asset_id).status.value
            if link.asset_id in _executors else None,
        })
    return {"items": items, "total": len(items)}


@app.get("/vehicles/{asset_id}")
async def get_vehicle_state(asset_id: str) -> dict:
    link = _link_or_404(asset_id)
    return {
        "asset_id": link.asset_id,
        "link_state": link.link_state.value,
        "state": link.state.to_dict(),
        "mission_status": _executors.get(asset_id).status.value if asset_id in _executors else None,
    }


@app.post("/vehicles/{asset_id}/connect")
async def connect_vehicle(asset_id: str, body: ConnectRequest) -> dict:
    if not validate_asset_id(asset_id):
        raise HTTPException(status_code=400, detail="Invalid asset_id")
    await link_registry.remove(asset_id)
    new_link = VehicleLink(asset_id=asset_id, system_address=body.address,
                           mavsdk_port=body.mavsdk_port)
    await link_registry.add(new_link)
    connect_task = asyncio.create_task(new_link.connect())
    new_link.add_task(connect_task)
    return {"ok": True, "asset_id": asset_id, "address": body.address}


@app.post("/vehicles/{asset_id}/disconnect")
async def disconnect_vehicle(asset_id: str) -> dict:
    if not validate_asset_id(asset_id):
        raise HTTPException(status_code=400, detail="Invalid asset_id")
    await link_registry.remove(asset_id)
    return {"ok": True, "asset_id": asset_id}


@app.post("/vehicles/{asset_id}/arm")
async def arm_vehicle(asset_id: str, body: ArmRequest) -> dict:
    bridge = await _bridge_or_404(asset_id)
    await bridge.poll_battery()
    await bridge.poll_gps()
    ok = await bridge.arm()
    if not ok:
        raise HTTPException(status_code=409, detail="Arm failed")
    return {"ok": True, "asset_id": asset_id}


@app.post("/vehicles/{asset_id}/disarm")
async def disarm_vehicle(asset_id: str) -> dict:
    bridge = await _bridge_or_404(asset_id)
    ok = await bridge.disarm()
    if not ok:
        raise HTTPException(status_code=409, detail="Disarm failed")
    return {"ok": True, "asset_id": asset_id}


@app.post("/vehicles/{asset_id}/takeoff")
async def takeoff(asset_id: str, altitude_m: float = 10.0) -> dict:
    bridge = await _bridge_or_404(asset_id)
    ok = await bridge.takeoff(altitude_m)
    if not ok:
        raise HTTPException(status_code=409, detail="Takeoff failed")
    return {"ok": True, "asset_id": asset_id, "altitude_m": altitude_m}


@app.post("/vehicles/{asset_id}/land")
async def land(asset_id: str) -> dict:
    bridge = await _bridge_or_404(asset_id)
    ok = await bridge.land()
    if not ok:
        raise HTTPException(status_code=409, detail="Land command failed")
    return {"ok": True, "asset_id": asset_id}


@app.post("/vehicles/{asset_id}/rtl")
async def return_to_launch(asset_id: str) -> dict:
    bridge = await _bridge_or_404(asset_id)
    ok = await bridge.rtl()
    if not ok:
        raise HTTPException(status_code=409, detail="RTL command failed")
    return {"ok": True, "asset_id": asset_id}


@app.post("/vehicles/{asset_id}/emergency-stop")
async def emergency_stop(asset_id: str) -> dict:
    """Immediate disarm. Only for when the aircraft poses immediate danger."""
    link = _link_or_404(asset_id)
    if link.link_state == LinkState.CONNECTED:
        bridge = bridge_registry.get(asset_id) or await bridge_registry.create(asset_id)
        if bridge:
            ok = await bridge.emergency_stop()
            if not ok:
                raise HTTPException(status_code=409, detail="Emergency stop failed")
    return {"ok": True, "asset_id": asset_id, "scope": "single"}


@app.post("/vehicles/{asset_id}/mission")
async def run_mission(asset_id: str, body: MissionRequest) -> dict:
    link = _link_or_404(asset_id)
    if link.link_state != LinkState.CONNECTED:
        raise HTTPException(status_code=409, detail="Vehicle not connected")
    if asset_id in _executors and _executors[asset_id].status not in (
        MissionStatus.COMPLETED, MissionStatus.ABORTED, MissionStatus.FAILED, MissionStatus.LANDED,
    ):
        raise HTTPException(status_code=409, detail="Mission already in progress")

    if not body.waypoints:
        raise HTTPException(status_code=400, detail="Waypoints required")

    bridge = bridge_registry.get(asset_id) or await bridge_registry.create(asset_id)
    if bridge is None:
        raise HTTPException(status_code=409, detail="Vehicle bridge unavailable")
    if not bridge.link.is_usable:
        raise HTTPException(status_code=409, detail="Link not usable")

    await bridge.start_telemetry_stream()
    telemetry_subscriber.start(link)

    env = SafetyEnvelope(**body.envelope)
    plan = MissionPlan(
        mission_id=body.mission_id,
        asset_id=asset_id,
        flight_id=body.flight_id,
        waypoints=body.waypoints,
        envelope=env,
        approved=body.approved,
        approved_by=body.approved_by,
    )
    executor = MissionExecutor(link, plan, status_callback=_make_status_callback())
    _executors[asset_id] = executor
    task = asyncio.create_task(executor.run())
    link.add_task(task)
    return {"ok": True, "asset_id": asset_id, "status": executor.status.value,
            "mission_id": body.mission_id, "flight_id": body.flight_id}


@app.post("/vehicles/{asset_id}/mission/cancel")
async def cancel_mission(asset_id: str) -> dict:
    if not validate_asset_id(asset_id):
        raise HTTPException(status_code=400, detail="Invalid asset_id")
    executor = _executors.get(asset_id)
    if executor is None:
        raise HTTPException(status_code=404, detail="No active mission")
    await executor.cancel()
    return {"ok": True, "asset_id": asset_id, "status": executor.status.value}


@app.get("/vehicles/{asset_id}/mission")
async def get_mission_status(asset_id: str) -> dict:
    if not validate_asset_id(asset_id):
        raise HTTPException(status_code=400, detail="Invalid asset_id")
    executor = _executors.get(asset_id)
    if executor is None:
        raise HTTPException(status_code=404, detail="No mission state")
    return {
        "asset_id": asset_id,
        "status": executor.status.value,
        "progress": executor._progress,
    }