"""
Asset service: CRUD and status for drones, ground vehicles, sensors.
Region-scoped filtering for local operators is applied via X-Region-Ids header.
"""
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import UUID

from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.responses import JSONResponse
import asyncpg

from defense_shared.schemas import AssetCreate, AssetResponse, AssetStatus, AssetType, Role

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense")
pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return pool


def get_region_filter(
    x_region_ids: str | None = Header(None, alias="X-Region-Ids"),
    x_user_role: str | None = Header(None, alias="X-User-Role"),
) -> list[str] | None:
    """For LOCAL_OPERATOR, only return assets in these regions. Super admin gets all."""
    if x_user_role == Role.SUPER_ADMIN.value:
        return None
    if not x_region_ids:
        return []
    return [r.strip() for r in x_region_ids.split(",") if r.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    if pool:
        await pool.close()


app = FastAPI(title="Asset Service", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "asset-service"}


@app.get("/assets")
async def list_assets(
    region_id: str | None = None,
    status: str | None = None,
    asset_type: str | None = None,
    regions: list[str] | None = Depends(get_region_filter),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    conditions = []
    args = []
    idx = 1
    if regions is not None and len(regions) == 0:
        return {"items": [], "total": 0}
    if regions is not None:
        conditions.append(f"region_id = ANY(${idx})")
        args.append(regions)
        idx += 1
    if region_id:
        conditions.append(f"region_id = ${idx}")
        args.append(region_id)
        idx += 1
    if status:
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1
    if asset_type:
        conditions.append(f"asset_type = ${idx}")
        args.append(asset_type)
        idx += 1
    where = " AND ".join(conditions) if conditions else "TRUE"
    q = f"SELECT id, name, asset_type, region_id, status, metadata, tags, created_at, updated_at FROM assets.assets WHERE {where} ORDER BY updated_at DESC"
    rows = await db.fetch(q, *args)
    items = [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "asset_type": r["asset_type"],
            "region_id": r["region_id"],
            "status": r["status"],
            "metadata": r["metadata"] or {},
            "tags": list(r["tags"] or []),
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}


@app.post("/assets", status_code=201)
async def create_asset(
    body: AssetCreate,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    row = await db.fetchrow(
        """
        INSERT INTO assets.assets (name, asset_type, region_id, metadata, tags)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, name, asset_type, region_id, status, metadata, tags, created_at, updated_at
        """,
        body.name,
        body.asset_type.value,
        body.region_id,
        body.metadata,
        body.tags,
    )
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "asset_type": row["asset_type"],
        "region_id": row["region_id"],
        "status": row["status"],
        "metadata": row["metadata"] or {},
        "tags": list(row["tags"] or []),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


@app.patch("/assets/{asset_id}/status")
async def update_asset_status(
    asset_id: UUID,
    body: dict,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    status = body.get("status")
    if status not in (s.value for s in AssetStatus):
        raise HTTPException(status_code=400, detail="Invalid status")
    row = await db.fetchrow(
        "UPDATE assets.assets SET status = $1, updated_at = NOW() WHERE id = $2 RETURNING id, status, updated_at",
        status,
        asset_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Asset not found")
    return {"id": str(row["id"]), "status": row["status"], "updated_at": row["updated_at"].isoformat()}
