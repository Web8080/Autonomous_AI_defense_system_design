"""
Model registry service: provenance, evaluation gate, and promotion.

The `ml.models` / `ml.model_evals` tables from Phase 0 become real here:

- `POST /models` registers a model with its full provenance chain (git SHA,
  dataset hash, artifact). A model row is required before any training run is
  worth recording.
- `POST /models/{id}/evals` records an evaluation against the frozen benchmark.
  `passed_gate` is computed in code by `eval_gate.py`, never by the client.
- `POST /models/{id}/promote` is the money path: it re-checks the absolute gate
  on the model's latest passing eval, runs the product-metric regression check
  against the *current* production model, and only then moves the stage.
  Promoting to production archives the previous production model, so at any
  moment exactly one model is `production`.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Literal
from uuid import UUID

import asyncpg
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from eval_gate import EvalRecord, GateConfig, evaluate_gate, check_regression

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense")

pool: asyncpg.Pool | None = None

VALID_TASKS = {"detection", "segmentation", "classification", "thermal", "tracking"}
VALID_RUNTIMES = {"pytorch", "onnx", "tensorrt", "openvino"}
VALID_PRECISIONS = {"fp32", "fp16", "int8"}
VALID_STAGES = {"development", "staging", "production", "archived"}

GATE = GateConfig()


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


app = FastAPI(title="ML Model Registry", lifespan=lifespan)


@app.exception_handler(asyncpg.ForeignKeyViolationError)
async def _fk_violation(request, exc: asyncpg.ForeignKeyViolationError) -> JSONResponse:
    detail = exc.args[0] if exc.args else "Referenced record not found"
    return JSONResponse(status_code=400, content={"detail": detail})


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ModelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    version: str = Field(..., min_length=1, max_length=64)
    task: Literal["detection", "segmentation", "classification", "thermal", "tracking"] = "detection"
    architecture: str | None = Field(None, max_length=128)
    git_sha: str | None = Field(None, max_length=64)
    dataset_hash: str | None = Field(None, max_length=128)
    mlflow_run_id: str | None = Field(None, max_length=128)
    artifact_uri: str | None = None
    runtime: Literal["pytorch", "onnx", "tensorrt", "openvino"] = "pytorch"
    precision: Literal["fp32", "fp16", "int8"] = "fp32"
    class_names: list[str] = Field(default_factory=list, max_length=512)


class EvalCreate(BaseModel):
    benchmark: str = Field(..., min_length=1)
    benchmark_hash: str = Field(..., min_length=1)
    map_50: float | None = Field(None, ge=0, le=1)
    map_50_95: float | None = Field(None, ge=0, le=1)
    precision_at_conf: float | None = Field(None, ge=0, le=1)
    recall_at_conf: float | None = Field(None, ge=0, le=1)
    false_alarms_per_hour: float | None = Field(None, ge=0)
    recall_at_target_far: float | None = Field(None, ge=0, le=1)
    device: str | None = Field(None, max_length=64)
    latency_p50_ms: float | None = Field(None, ge=0)
    latency_p95_ms: float | None = Field(None, ge=0)
    per_class: dict = Field(default_factory=dict)
    per_slice: dict = Field(default_factory=dict)


class PromoteRequest(BaseModel):
    # Named actor, required and FK-constrained against auth.users: promotion is
    # a recorded human decision, exactly like flight approval.
    promoted_by: UUID
    note: str | None = None


class StageRequest(BaseModel):
    stage: str


# ---------------------------------------------------------------------------
# Assessment: the gate as a pure function over a request body (unit-testable)
# ---------------------------------------------------------------------------

def assess(body: dict, class_names: list[str], incumbent: dict | None = None) -> dict:
    """Run the absolute gate plus the incumbent regression check over an eval body.

    `passed` is False unless *both* the absolute promotion criteria and the
    no-regression-vs-production criteria hold.
    """
    record = EvalRecord.from_mapping(body, class_names)
    decision = evaluate_gate(record, GATE)
    regressions = check_regression(
        record,
        EvalRecord.from_mapping(incumbent, [] if incumbent is None else (incumbent.get("class_names") or [])),
    ) if incumbent else []
    reasons = decision.reasons + regressions
    return {
        "passed": decision.passed and not regressions,
        "gate_notes": "; ".join(reasons) if reasons else "promotion criteria satisfied",
        "absolute_passed": decision.passed,
        "regressions": regressions,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _model_to_dict(r: asyncpg.Record) -> dict:
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "version": r["version"],
        "task": r["task"],
        "architecture": r["architecture"],
        "git_sha": r["git_sha"],
        "dataset_hash": r["dataset_hash"],
        "mlflow_run_id": r["mlflow_run_id"],
        "artifact_uri": r["artifact_uri"],
        "runtime": r["runtime"],
        "precision": r["precision"],
        "class_names": r["class_names"] or [],
        "stage": r["stage"],
        "promoted_by": str(r["promoted_by"]) if r["promoted_by"] else None,
        "promoted_at": r["promoted_at"].isoformat() if r["promoted_at"] else None,
        "created_at": r["created_at"].isoformat(),
    }


def _eval_to_dict(r: asyncpg.Record) -> dict:
    out = {k: r[k] for k in (
        "id", "model_id", "benchmark", "benchmark_hash", "map_50", "map_50_95",
        "precision_at_conf", "recall_at_conf", "false_alarms_per_hour",
        "recall_at_target_far", "device", "latency_p50_ms", "latency_p95_ms",
    )}
    for k in ("map_50", "map_50_95", "precision_at_conf", "recall_at_conf",
              "false_alarms_per_hour", "recall_at_target_far", "latency_p50_ms",
              "latency_p95_ms"):
        out[k] = float(out[k]) if out[k] is not None else None
    out["model_id"] = str(out["model_id"])
    out["per_class"] = r["per_class"] or {}
    out["per_slice"] = r["per_slice"] or {}
    out["passed_gate"] = r["passed_gate"]
    out["gate_notes"] = r["gate_notes"]
    out["created_at"] = r["created_at"].isoformat()
    return out


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "ml-service"}


@app.post("/models", status_code=201)
async def register_model(body: ModelCreate, db: asyncpg.Pool = Depends(get_pool)) -> dict:
    if body.task not in VALID_TASKS:
        raise HTTPException(status_code=422, detail=f"task must be one of {sorted(VALID_TASKS)}")
    if body.runtime not in VALID_RUNTIMES:
        raise HTTPException(status_code=422, detail=f"runtime must be one of {sorted(VALID_RUNTIMES)}")
    if body.precision not in VALID_PRECISIONS:
        raise HTTPException(status_code=422, detail=f"precision must be one of {sorted(VALID_PRECISIONS)}")
    row = await db.fetchrow(
        """
        INSERT INTO ml.models
            (name, version, task, architecture, git_sha, dataset_hash, mlflow_run_id,
             artifact_uri, runtime, precision, class_names, stage)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'development')
        RETURNING *
        """,
        body.name, body.version, body.task, body.architecture, body.git_sha,
        body.dataset_hash, body.mlflow_run_id, body.artifact_uri, body.runtime,
        body.precision, body.class_names,
    )
    return _model_to_dict(row)


@app.get("/models")
async def list_models(
    stage: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    if stage and stage not in VALID_STAGES:
        raise HTTPException(status_code=422, detail=f"stage must be one of {sorted(VALID_STAGES)}")
    if stage:
        rows = await db.fetch(
            "SELECT * FROM ml.models WHERE stage = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            stage, limit, offset)
    else:
        rows = await db.fetch(
            "SELECT * FROM ml.models ORDER BY created_at DESC LIMIT $1 OFFSET $2", limit, offset)
    return {"items": [_model_to_dict(r) for r in rows], "total": len(rows)}


@app.get("/models/production")
async def get_production_model(db: asyncpg.Pool = Depends(get_pool)) -> dict:
    """The model the inference fleet should be running right now.

    Exact statement of the traceability promise: exactly one production model
    exists, and it has a passing eval on record.
    """
    row = await db.fetchrow("SELECT * FROM ml.models WHERE stage = 'production'")
    if not row:
        raise HTTPException(status_code=404, detail="No production model registered")
    model = _model_to_dict(row)
    evals = await db.fetch(
        "SELECT * FROM ml.model_evals WHERE model_id = $1 AND passed_gate ORDER BY created_at DESC LIMIT 1",
        row["id"])
    model["certified_eval"] = _eval_to_dict(evals[0]) if evals else None
    return model


@app.get("/models/{model_id}")
async def get_model(model_id: UUID, db: asyncpg.Pool = Depends(get_pool)) -> dict:
    row = await db.fetchrow("SELECT * FROM ml.models WHERE id = $1", model_id)
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    return _model_to_dict(row)


@app.patch("/models/{model_id}")
async def update_model(model_id: UUID, body: dict, db: asyncpg.Pool = Depends(get_pool)) -> dict:
    allowed = {"name", "architecture", "git_sha", "dataset_hash", "mlflow_run_id",
               "artifact_uri", "class_names"}
    updates = [(k, v) for k, v in body.items() if k in allowed]
    if not updates:
        raise HTTPException(status_code=400, detail="No updatable fields")
    sets = ", ".join(f"{k} = ${i}" for i, (k, _) in enumerate(updates, start=1))
    args = [v for _, v in updates] + [model_id]
    row = await db.fetchrow(
        f"UPDATE ml.models SET {sets} WHERE id = ${len(updates) + 1} RETURNING *", *args)
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    return _model_to_dict(row)


# ---------------------------------------------------------------------------
# Evaluations
# ---------------------------------------------------------------------------

@app.post("/models/{model_id}/evals", status_code=201)
async def record_eval(
    model_id: UUID,
    body: EvalCreate,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    model = await db.fetchrow("SELECT * FROM ml.models WHERE id = $1", model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    incumbent = None
    prod = await db.fetchrow("SELECT * FROM ml.models WHERE stage = 'production' AND id <> $1", model_id)
    if prod:
        inc_eval = await db.fetchrow(
            "SELECT * FROM ml.model_evals WHERE model_id = $1 AND passed_gate "
            "ORDER BY created_at DESC LIMIT 1", prod["id"])
        if inc_eval:
            incumbent = {
                "recall_at_target_far": inc_eval["recall_at_target_far"],
                "false_alarms_per_hour": inc_eval["false_alarms_per_hour"],
            }

    # The gate runs here, on the server. A client that sends passed_gate=true
    # but bad metrics cannot win: passed_gate is recomputed and overwritten.
    result = assess(body.model_dump(), list(model["class_names"] or []), incumbent)
    row = await db.fetchrow(
        """
        INSERT INTO ml.model_evals
            (model_id, benchmark, benchmark_hash, map_50, map_50_95,
             precision_at_conf, recall_at_conf, false_alarms_per_hour,
             recall_at_target_far, device, latency_p50_ms, latency_p95_ms,
             per_class, per_slice, passed_gate, gate_notes)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                $13::jsonb, $14::jsonb, $15, $16)
        RETURNING *
        """,
        model_id, body.benchmark, body.benchmark_hash, body.map_50, body.map_50_95,
        body.precision_at_conf, body.recall_at_conf, body.false_alarms_per_hour,
        body.recall_at_target_far, body.device, body.latency_p50_ms, body.latency_p95_ms,
        json.dumps(body.per_class), json.dumps(body.per_slice), result["passed"], result["gate_notes"],
    )
    return _eval_to_dict(row)


@app.get("/models/{model_id}/evals")
async def list_evals(
    model_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    model = await db.fetchrow("SELECT id FROM ml.models WHERE id = $1", model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    rows = await db.fetch(
        "SELECT * FROM ml.model_evals WHERE model_id = $1 ORDER BY created_at DESC LIMIT $2",
        model_id, limit)
    return {"items": [_eval_to_dict(r) for r in rows], "total": len(rows)}


# ---------------------------------------------------------------------------
# Promotion / stage transitions
# ---------------------------------------------------------------------------

async def _latest_passing_eval(db: asyncpg.Pool, model_id: UUID):
    return await db.fetchrow(
        "SELECT * FROM ml.model_evals WHERE model_id = $1 AND passed_gate "
        "ORDER BY created_at DESC LIMIT 1", model_id)


@app.post("/models/{model_id}/promote")
async def promote_model(
    model_id: UUID,
    body: PromoteRequest,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """The recorded human promotion gate.

    1. The model must exist.
    2. A passing eval must exist on record (the absolute gate already passed
       when the eval was recorded against the frozen benchmark).
    3. The regression check is re-run at promotion time against the current
       production model, because the incumbent may have changed since the eval.
    4. On success, promote to `production` and archive any prior production
       model, atomically.
    """
    model = await db.fetchrow("SELECT * FROM ml.models WHERE id = $1", model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    if model["stage"] == "archived":
        raise HTTPException(status_code=409, detail="Archived models cannot be promoted")

    eval_row = await _latest_passing_eval(db, model_id)
    if not eval_row:
        raise HTTPException(
            status_code=403,
            detail="No passing evaluation on record. The model cannot reach "
                   "production without a recorded eval that satisfies the gate.",
        )

    incumbent = await db.fetchrow("SELECT * FROM ml.models WHERE stage = 'production' AND id <> $1", model_id)
    regressions: list[str] = []
    if incumbent:
        inc_eval = await db.fetchrow(
            "SELECT * FROM ml.model_evals WHERE model_id = $1 AND passed_gate "
            "ORDER BY created_at DESC LIMIT 1", incumbent["id"])
        if inc_eval:
            regressions = check_regression(
                EvalRecord.from_mapping(eval_row, list(model["class_names"] or [])),
                EvalRecord.from_mapping(inc_eval, list(incumbent["class_names"] or [])),
            )
    if regressions:
        raise HTTPException(
            status_code=409,
            detail="Promotion would regress a product metric vs the incumbent: "
                   + "; ".join(regressions),
        )

    async with db.acquire() as conn:
        async with conn.transaction():
            if incumbent:
                await conn.execute(
                    "UPDATE ml.models SET stage = 'archived' WHERE id = $1", incumbent["id"])
            updated = await conn.fetchrow(
                """
                UPDATE ml.models
                SET stage = 'production', promoted_by = $1, promoted_at = NOW()
                WHERE id = $2
                RETURNING *
                """,
                body.promoted_by, model_id,
            )
    return {
        **_model_to_dict(updated),
        "certified_by_eval_id": str(eval_row["id"]),
        "promoted_by": str(body.promoted_by),
        "note": body.note,
    }


@app.post("/models/{model_id}/stage")
async def set_stage(
    model_id: UUID,
    body: StageRequest,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Non-production stage moves (development <-> staging). Production is
    reached only through /promote, which runs the gate."""
    if body.stage not in VALID_STAGES:
        raise HTTPException(status_code=422, detail=f"stage must be one of {sorted(VALID_STAGES)}")
    if body.stage == "production":
        raise HTTPException(status_code=400, detail="Use /promote to reach production")
    row = await db.fetchrow(
        "UPDATE ml.models SET stage = $1 WHERE id = $2 RETURNING *",
        body.stage, model_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    return _model_to_dict(row)