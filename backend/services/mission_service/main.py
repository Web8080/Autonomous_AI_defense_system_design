"""
Mission service: missions, waypoints, flights, and the dispatch path.

- Missions and waypoints: CRUD, site-scoped.
- Flights: one row per execution. `requires_approval` is enforced before any
  aircraft ever arms; the approval is recorded against the flight.
- Dispatch: when a flight is started, the waypoints are sent to the drone
  bridge, which is the only service that talks MAVLink.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from uuid import UUID

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import asyncpg
import httpx

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense")
DRONE_BRIDGE_URL = os.getenv("DRONE_BRIDGE_URL", "http://drone-bridge:8000")

pool: asyncpg.Pool | None = None
_client: httpx.AsyncClient | None = None


async def get_pool() -> asyncpg.Pool:
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _client
    _client = httpx.AsyncClient(base_url=DRONE_BRIDGE_URL, timeout=15.0)
    yield
    if _client:
        await _client.aclose()
    if pool:
        await pool.close()


app = FastAPI(title="Mission Service", lifespan=lifespan)


@app.exception_handler(asyncpg.ForeignKeyViolationError)
async def _fk_violation(request, exc: asyncpg.ForeignKeyViolationError) -> JSONResponse:
    detail = exc.args[0] if exc.args else "Referenced record not found"
    return JSONResponse(status_code=400, content={"detail": detail})


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class WaypointCreate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    altitude_m: float = Field(..., gt=0, le=500)
    speed_ms: float | None = Field(None, gt=0, le=50)
    action: str = "waypoint"
    loiter_seconds: int = 0
    heading_deg: float | None = Field(None, ge=0, le=360)
    gimbal_pitch_deg: float | None = Field(None, ge=-90, le=90)


class MissionCreate(BaseModel):
    org_id: UUID
    site_id: UUID
    name: str
    description: str | None = None
    mission_type: str = "patrol"
    schedule_cron: str | None = None
    default_altitude_m: float = 60.0
    default_speed_ms: float = 5.0
    requires_approval: bool = True
    waypoints: list[WaypointCreate] = Field(default_factory=list)


class FlightCreate(BaseModel):
    org_id: UUID
    site_id: UUID
    mission_id: UUID
    asset_id: UUID
    trigger_kind: str = "manual"
    notes: str | None = None


class FlightApprove(BaseModel):
    approved_by: UUID
    note: str | None = None


class FlightStart(BaseModel):
    envelope: dict[str, Any] = Field(default_factory=dict)


class FlightStatusUpdate(BaseModel):
    status: str
    started_at: str | None = None
    ended_at: str | None = None
    abort_reason: str | None = None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "mission-service"}


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------

def _mission_to_dict(r: asyncpg.Record) -> dict:
    return {
        "id": str(r["id"]),
        "org_id": str(r["org_id"]),
        "site_id": str(r["site_id"]),
        "name": r["name"],
        "description": r["description"],
        "mission_type": r["mission_type"],
        "schedule_cron": r["schedule_cron"],
        "default_altitude_m": float(r["default_altitude_m"]),
        "default_speed_ms": float(r["default_speed_ms"]),
        "requires_approval": r["requires_approval"],
        "active": r["active"],
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    }


@app.get("/missions")
async def list_missions(
    site_id: UUID | None = None,
    org_id: UUID | None = None,
    active: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    conditions = []
    args: list[Any] = []
    idx = 1
    if site_id:
        conditions.append(f"site_id = ${idx}::uuid")
        args.append(site_id)
        idx += 1
    if org_id:
        conditions.append(f"org_id = ${idx}::uuid")
        args.append(org_id)
        idx += 1
    if active is not None:
        conditions.append(f"active = ${idx}")
        args.append(active)
        idx += 1
    where = " AND ".join(conditions) if conditions else "TRUE"
    args.extend([limit, offset])
    q = f"""
        SELECT * FROM missions.missions
        WHERE {where}
        ORDER BY updated_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
    """
    rows = await db.fetch(q, *args)
    items = [_mission_to_dict(r) for r in rows]
    return {"items": items, "total": len(items)}


@app.post("/missions", status_code=201)
async def create_mission(
    body: MissionCreate,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    row = await db.fetchrow(
        """
        INSERT INTO missions.missions
            (org_id, site_id, name, description, mission_type, schedule_cron,
             default_altitude_m, default_speed_ms, requires_approval)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING *
        """,
        body.org_id, body.site_id, body.name, body.description, body.mission_type,
        body.schedule_cron, body.default_altitude_m, body.default_speed_ms,
        body.requires_approval,
    )
    mission_id = row["id"]

    for seq, wp in enumerate(body.waypoints):
        await db.execute(
            """
            INSERT INTO missions.waypoints
                (mission_id, seq, position, altitude_m, speed_ms, action,
                 loiter_seconds, heading_deg, gimbal_pitch_deg)
            VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography,
                    $5, $6, $7, $8, $9, $10)
            """,
            mission_id, seq, wp.longitude, wp.latitude, wp.altitude_m, wp.speed_ms,
            wp.action, wp.loiter_seconds, wp.heading_deg, wp.gimbal_pitch_deg,
        )

    mission = _mission_to_dict(row)
    mission["waypoints"] = await _get_waypoints(db, mission_id)
    return mission


@app.get("/missions/{mission_id}")
async def get_mission(
    mission_id: UUID,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    row = await db.fetchrow("SELECT * FROM missions.missions WHERE id = $1", mission_id)
    if not row:
        raise HTTPException(status_code=404, detail="Mission not found")
    mission = _mission_to_dict(row)
    mission["waypoints"] = await _get_waypoints(db, mission_id)
    return mission


@app.patch("/missions/{mission_id}")
async def update_mission(
    mission_id: UUID,
    body: dict,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    allowed = {
        "name", "description", "mission_type", "schedule_cron",
        "default_altitude_m", "default_speed_ms", "requires_approval", "active",
    }
    updates = [(k, v) for k, v in body.items() if k in allowed]
    if not updates:
        raise HTTPException(status_code=400, detail="No updatable fields")

    sets = []
    args: list[Any] = []
    idx = 1
    for k, v in updates:
        sets.append(f"{k} = ${idx}")
        args.append(v)
        idx += 1
    args.append(mission_id)
    row = await db.fetchrow(
        f"UPDATE missions.missions SET {', '.join(sets)}, updated_at = NOW() "
        f"WHERE id = ${idx} RETURNING *",
        *args,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Mission not found")
    mission = _mission_to_dict(row)
    mission["waypoints"] = await _get_waypoints(db, mission_id)
    return mission


@app.delete("/missions/{mission_id}", status_code=204)
async def delete_mission(
    mission_id: UUID,
    db: asyncpg.Pool = Depends(get_pool),
) -> None:
    # Waypoints cascade via ON DELETE CASCADE.
    deleted = await db.fetchrow(
        "DELETE FROM missions.missions WHERE id = $1 RETURNING id", mission_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Mission not found")


# ---------------------------------------------------------------------------
# Waypoints
# ---------------------------------------------------------------------------

async def _get_waypoints(db: asyncpg.Pool, mission_id: UUID) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT seq, ST_Y(position::geometry) AS latitude,
               ST_X(position::geometry) AS longitude,
               altitude_m, speed_ms, action, loiter_seconds,
               heading_deg, gimbal_pitch_deg
        FROM missions.waypoints
        WHERE mission_id = $1
        ORDER BY seq
        """,
        mission_id,
    )
    return [
        {
            "seq": r["seq"],
            "latitude": round(float(r["latitude"]), 7),
            "longitude": round(float(r["longitude"]), 7),
            "altitude_m": float(r["altitude_m"]),
            "speed_ms": float(r["speed_ms"]) if r["speed_ms"] is not None else None,
            "action": r["action"],
            "loiter_seconds": r["loiter_seconds"],
            "heading_deg": float(r["heading_deg"]) if r["heading_deg"] is not None else None,
            "gimbal_pitch_deg": float(r["gimbal_pitch_deg"]) if r["gimbal_pitch_deg"] is not None else None,
        }
        for r in rows
    ]


@app.put("/missions/{mission_id}/waypoints")
async def replace_waypoints(
    mission_id: UUID,
    body: list[WaypointCreate],
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    row = await db.fetchrow("SELECT id FROM missions.missions WHERE id = $1", mission_id)
    if not row:
        raise HTTPException(status_code=404, detail="Mission not found")

    await db.execute("DELETE FROM missions.waypoints WHERE mission_id = $1", mission_id)
    for seq, wp in enumerate(body):
        await db.execute(
            """
            INSERT INTO missions.waypoints
                (mission_id, seq, position, altitude_m, speed_ms, action,
                 loiter_seconds, heading_deg, gimbal_pitch_deg)
            VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography,
                    $5, $6, $7, $8, $9, $10)
            """,
            mission_id, seq, wp.longitude, wp.latitude, wp.altitude_m, wp.speed_ms,
            wp.action, wp.loiter_seconds, wp.heading_deg, wp.gimbal_pitch_deg,
        )
    return {"waypoints": await _get_waypoints(db, mission_id)}


# ---------------------------------------------------------------------------
# Flights
# ---------------------------------------------------------------------------

def _flight_to_dict(r: asyncpg.Record) -> dict:
    return {
        "id": str(r["id"]),
        "org_id": str(r["org_id"]),
        "site_id": str(r["site_id"]),
        "mission_id": str(r["mission_id"]) if r["mission_id"] else None,
        "asset_id": str(r["asset_id"]),
        "status": r["status"],
        "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
        "approved_at": r["approved_at"].isoformat() if r["approved_at"] else None,
        "trigger_kind": r["trigger_kind"],
        "started_at": r["started_at"].isoformat() if r["started_at"] else None,
        "ended_at": r["ended_at"].isoformat() if r["ended_at"] else None,
        "abort_reason": r["abort_reason"],
        "created_at": r["created_at"].isoformat(),
    }


@app.get("/flights")
async def list_flights(
    asset_id: UUID | None = None,
    mission_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    conditions = []
    args: list[Any] = []
    idx = 1
    if asset_id:
        conditions.append(f"asset_id = ${idx}::uuid")
        args.append(asset_id)
        idx += 1
    if mission_id:
        conditions.append(f"mission_id = ${idx}::uuid")
        args.append(mission_id)
        idx += 1
    if status:
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1
    where = " AND ".join(conditions) if conditions else "TRUE"
    args.append(limit)
    q = f"""
        SELECT * FROM missions.flights
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ${idx}
    """
    rows = await db.fetch(q, *args)
    return {"items": [_flight_to_dict(r) for r in rows], "total": len(rows)}


@app.post("/flights", status_code=201)
async def create_flight(
    body: FlightCreate,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    mission = await db.fetchrow(
        "SELECT mission_type, requires_approval FROM missions.missions WHERE id = $1",
        body.mission_id,
    )
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    asset = await db.fetchrow(
        "SELECT id FROM assets.assets WHERE id = $1",
        body.asset_id,
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    status = "awaiting_approval" if mission["requires_approval"] else "pending"
    row = await db.fetchrow(
        """
        INSERT INTO missions.flights
            (org_id, site_id, mission_id, asset_id, status, trigger_kind)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        body.org_id, body.site_id, body.mission_id, body.asset_id, status, body.trigger_kind,
    )
    return _flight_to_dict(row)


@app.get("/flights/{flight_id}")
async def get_flight(
    flight_id: UUID,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    row = await db.fetchrow("SELECT * FROM missions.flights WHERE id = $1", flight_id)
    if not row:
        raise HTTPException(status_code=404, detail="Flight not found")
    flight = _flight_to_dict(row)
    if row["mission_id"]:
        flight["mission"] = await _mission_to_dict_async(db, row["mission_id"])
    return flight


async def _mission_to_dict_async(db: asyncpg.Pool, mission_id: UUID) -> dict:
    row = await db.fetchrow("SELECT * FROM missions.missions WHERE id = $1", mission_id)
    mission = _mission_to_dict(row)
    mission["waypoints"] = await _get_waypoints(db, mission_id)
    return mission


@app.post("/flights/{flight_id}/approve")
async def approve_flight(
    flight_id: UUID,
    body: FlightApprove,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """The human oversight gate: a named user approves before any aircraft arms."""
    row = await db.fetchrow(
        """
        UPDATE missions.flights
        SET status = 'approved', approved_by = $1, approved_at = NOW()
        WHERE id = $2 AND status IN ('awaiting_approval', 'approved')
        RETURNING *
        """,
        body.approved_by, flight_id,
    )
    if not row:
        raise HTTPException(
            status_code=409,
            detail="Flight not found or not in a state that can be approved",
        )
    return _flight_to_dict(row)


@app.post("/flights/{flight_id}/status")
async def update_flight_status(
    flight_id: UUID,
    body: FlightStatusUpdate,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Status callback endpoint, called by the drone bridge as a mission progresses."""
    allowed = {
        "pending", "awaiting_approval", "approved", "armed",
        "in_flight", "returning", "completed", "aborted", "failed",
    }
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid status {body.status!r}")

    if body.status in ("completed", "aborted", "failed"):
        row = await db.fetchrow(
            """
            UPDATE missions.flights
            SET status = $1, ended_at = COALESCE($3, NOW()), abort_reason = $4
            WHERE id = $2
            RETURNING *
            """,
            body.status, flight_id, body.ended_at, body.abort_reason,
        )
    else:
        row = await db.fetchrow(
            """
            UPDATE missions.flights
            SET status = $1,
                started_at = COALESCE(
                    started_at,
                    CASE WHEN $3 IS NOT NULL THEN $3::timestamptz ELSE NOW() END
                )
            WHERE id = $2
            RETURNING *
            """,
            body.status, flight_id, body.started_at,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Flight not found")
    return _flight_to_dict(row)


@app.post("/flights/{flight_id}/start")
async def start_flight(
    flight_id: UUID,
    body: FlightStart,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Dispatch this flight to the drone bridge. Requires approval first."""
    row = await db.fetchrow("SELECT * FROM missions.flights WHERE id = $1", flight_id)
    if not row:
        raise HTTPException(status_code=404, detail="Flight not found")

    mission_id = row["mission_id"]
    if not mission_id:
        raise HTTPException(status_code=400, detail="Flight has no mission")

    mission = await db.fetchrow(
        "SELECT requires_approval FROM missions.missions WHERE id = $1", mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    if mission["requires_approval"] and row["approved_by"] is None:
        raise HTTPException(
            status_code=403,
            detail="Flight is not approved. A named operator must approve before dispatch.",
        )

    waypoints = await _get_waypoints(db, mission_id)
    if not waypoints:
        raise HTTPException(status_code=400, detail="Mission has no waypoints")

    if _client is None:
        raise HTTPException(status_code=503, detail="Drone bridge unavailable")

    await db.execute(
        "UPDATE missions.flights SET status = 'armed' WHERE id = $1", flight_id)

    payload = {
        "mission_id": str(mission_id),
        "flight_id": str(flight_id),
        "approved": True,
        "approved_by": str(row["approved_by"]) if row["approved_by"] else None,
        "waypoints": waypoints,
        "envelope": body.envelope,
    }
    try:
        r = await _client.post(f"/vehicles/{row['asset_id']}/mission", json=payload)
    except Exception as exc:
        await db.execute(
            "UPDATE missions.flights SET status = 'failed', abort_reason = $1 WHERE id = $2",
            f"drone-bridge unreachable: {exc}", flight_id)
        raise HTTPException(status_code=502, detail="Drone bridge unreachable")

    if r.status_code != 200:
        await db.execute(
            "UPDATE missions.flights SET status = 'failed', abort_reason = $1 WHERE id = $2",
            f"dispatch refused: {r.text[:200]}", flight_id)
        raise HTTPException(status_code=r.status_code, detail=r.json().get("detail", r.text))

    await db.execute(
        "UPDATE missions.flights SET status = 'in_flight', started_at = NOW() WHERE id = $1",
        flight_id)
    return {"ok": True, "flight_id": str(flight_id), "drone_bridge": r.json()}