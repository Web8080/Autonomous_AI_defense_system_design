"""Billing meter stub (Phase 5 scaffold).

Author: Victor.I

In-memory counters only. No Stripe. Persist later behind the same API shape.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

MeterKind = Literal["flight_hours", "detections", "alerts", "storage_gb"]

app = FastAPI(title="Billing Service (stub)")
_meters: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
_events: list[dict] = []


class MeterEvent(BaseModel):
    org_id: str = Field(..., min_length=1, max_length=128)
    site_id: str | None = None
    kind: MeterKind
    quantity: float = Field(..., gt=0)
    note: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "billing-service", "mode": "stub"}


@app.post("/events", status_code=201)
def record_event(body: MeterEvent) -> dict:
    _meters[body.org_id][body.kind] += body.quantity
    evt = {
        "org_id": body.org_id,
        "site_id": body.site_id,
        "kind": body.kind,
        "quantity": body.quantity,
        "note": body.note,
        "ts": time.time(),
    }
    _events.append(evt)
    if len(_events) > 10_000:
        del _events[:1000]
    return {"ok": True, "totals": dict(_meters[body.org_id])}


@app.get("/orgs/{org_id}/usage")
def usage(org_id: str) -> dict:
    return {"org_id": org_id, "meters": dict(_meters.get(org_id, {}))}


@app.post("/stripe/webhook")
def stripe_webhook() -> dict:
    # Live Stripe stays optional / pre-deploy. Fail closed for accidental calls.
    raise HTTPException(
        status_code=501,
        detail="Stripe webhook not enabled — set up live billing only at deploy gate",
    )
