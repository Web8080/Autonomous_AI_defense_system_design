import pytest
from httpx import ASGITransport, AsyncClient

import sys
import os
from pathlib import Path
from conftest import load_service_app
from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

app = load_service_app("mission_service")


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json().get("service") == "mission-service"


@pytest.mark.asyncio
async def test_list_missions_rejects_bad_limit():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # No DB in unit tests; but the query validator should reject over-long limit first
        r = await client.get("/missions", params={"limit": 1000000})
        assert r.status_code == 422


def test_waypoint_schema_rejects_bad_coords():
    from services.mission_service.main import WaypointCreate
    with pytest.raises(ValidationError):
        WaypointCreate(latitude=91.0, longitude=0.0, altitude_m=40.0)
    with pytest.raises(ValidationError):
        WaypointCreate(latitude=51.5, longitude=-0.12, altitude_m=-5.0)
    ok = WaypointCreate(latitude=51.5, longitude=-0.12, altitude_m=40.0, speed_ms=5.0)
    assert ok.altitude_m == 40.0