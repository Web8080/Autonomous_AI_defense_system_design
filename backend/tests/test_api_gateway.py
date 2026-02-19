import os
import sys
import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "api_gateway"))
from main import app


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json().get("service") == "api-gateway"


@pytest.mark.asyncio
async def test_assets_requires_auth():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/api/v1/assets")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_assets_with_bearer():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get(
            "/api/v1/assets",
            headers={"Authorization": "Bearer stub-token"},
        )
        # May be 200 (if asset service is mocked) or 502/503 when service down
        assert r.status_code in (200, 502, 503)
