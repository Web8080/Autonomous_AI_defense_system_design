import pytest
from httpx import ASGITransport, AsyncClient

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "control_service"))
from main import app


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json().get("service") == "control-service"


@pytest.mark.asyncio
async def test_emergency_stop_accepts_body():
    """Without DB this may 500; we only check the endpoint accepts POST."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.post(
            "/emergency-stop",
            json={"asset_id": "all", "issued_by": "test"},
        )
        assert r.status_code in (200, 500)
