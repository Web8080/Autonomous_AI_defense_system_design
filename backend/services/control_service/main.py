"""
Control service: issue commands to assets (emergency stop, override, path plan).
Logs every command to audit.command_log. MQTT/WebSocket/ROS are placeholders.
"""
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import UUID

from fastapi import FastAPI, Depends, HTTPException
import asyncpg

from defense_shared.schemas import CommandIntent, CommandRequest
from defense_shared.safeguardrails import is_allowed_intent, validate_command_payload, validate_asset_id
from defense_shared.security import sanitize_issued_by

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense")
MAX_AUDIT_LIMIT = 500

pool: asyncpg.Pool | None = None
MQTT_BROKER_URL = os.getenv("MQTT_BROKER_URL", "")


async def get_pool() -> asyncpg.Pool:
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    if pool:
        await pool.close()


app = FastAPI(title="Control Service", lifespan=lifespan)


def _send_to_asset(asset_id: str, intent: str, payload: dict) -> str:
    """Placeholder: publish to MQTT or call drone API. Do not use real hardware without credentials."""
    if not MQTT_BROKER_URL and "PLACEHOLDER" in os.getenv("MQTT_BROKER_URL", ""):
        return "simulated"
    # TODO: MQTT publish or HTTP to drone/vehicle adapter
    return "sent"


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "control-service"}


@app.post("/emergency-stop")
async def emergency_stop(
    body: dict,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Critical path: halt all or specified assets. Always logged. Safeguardrails: asset_id validated."""
    asset_id = body.get("asset_id")
    issued_by = sanitize_issued_by(body.get("issued_by"))
    if not asset_id:
        asset_id = "all"
    else:
        asset_id = str(asset_id).strip()
        if not validate_asset_id(asset_id):
            raise HTTPException(status_code=400, detail="Invalid asset_id")
    result = _send_to_asset(asset_id, CommandIntent.EMERGENCY_STOP.value, {"scope": asset_id})
    await db.execute(
        """
        INSERT INTO audit.command_log (asset_id, intent, issued_by, is_override, payload, result)
        VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6)
        """,
        asset_id if asset_id != "all" else None,
        CommandIntent.EMERGENCY_STOP.value,
        issued_by,
        True,
        {"scope": asset_id},
        result,
    )
    return {"ok": True, "scope": asset_id, "result": result}


@app.post("/command")
async def send_command(
    body: CommandRequest,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Generic command: path plan, override, mission abort, etc. Logged. Safeguardrails: intent and payload validated."""
    if not validate_asset_id(body.asset_id):
        raise HTTPException(status_code=400, detail="Invalid asset_id")
    if not is_allowed_intent(body.intent.value):
        raise HTTPException(status_code=400, detail="Disallowed intent")
    ok, err = validate_command_payload(body.payload)
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    issued_by = sanitize_issued_by(body.issued_by)
    try:
        asset_uuid = UUID(body.asset_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid asset_id")
    result = _send_to_asset(body.asset_id, body.intent.value, body.payload)
    await db.execute(
        """
        INSERT INTO audit.command_log (asset_id, intent, issued_by, is_override, payload, result)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
        """,
        asset_uuid,
        body.intent.value,
        issued_by,
        body.is_override,
        body.payload,
        result,
    )
    return {"ok": True, "asset_id": body.asset_id, "intent": body.intent.value, "result": result}


@app.get("/audit")
async def list_audit(
    asset_id: str | None = None,
    limit: int = 100,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """List recent command log entries (for super admin / debugging). Limit capped for safety."""
    limit = min(max(1, limit), MAX_AUDIT_LIMIT)
    if asset_id and validate_asset_id(asset_id) and asset_id.strip().lower() != "all":
        rows = await db.fetch(
            "SELECT id, asset_id, intent, issued_by, is_override, payload, result, created_at FROM audit.command_log WHERE asset_id = $1::uuid ORDER BY created_at DESC LIMIT $2",
            asset_id,
            limit,
        )
    else:
        rows = await db.fetch(
            "SELECT id, asset_id, intent, issued_by, is_override, payload, result, created_at FROM audit.command_log ORDER BY created_at DESC LIMIT $1",
            limit,
        )
    items = [
        {
            "id": str(r["id"]),
            "asset_id": str(r["asset_id"]) if r["asset_id"] else None,
            "intent": r["intent"],
            "issued_by": r["issued_by"],
            "is_override": r["is_override"],
            "payload": r["payload"],
            "result": r["result"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}
