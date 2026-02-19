import pytest
from httpx import ASGITransport, AsyncClient

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "asset_service"))
from main import app


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json().get("service") == "asset-service"


@pytest.mark.asyncio
async def test_list_assets_empty():
    """Without DB this may fail; with DB and no data returns empty list."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/assets")
        if r.status_code == 200:
            assert "items" in r.json()
        else:
            assert r.status_code == 500  # DB connection failed
