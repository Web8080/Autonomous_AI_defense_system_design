import pytest
from httpx import ASGITransport, AsyncClient

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "inference_service"))
from main import app


@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json().get("service") == "inference-service"


@pytest.mark.asyncio
async def test_infer_requires_image():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.post(
            "/infer",
            json={"asset_id": "a", "frame_id": "f1", "timestamp": "2024-01-01T00:00:00Z"},
        )
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_infer_stub_returns_detections():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.post(
            "/infer",
            json={
                "asset_id": "a",
                "frame_id": "f1",
                "timestamp": "2024-01-01T00:00:00Z",
                "image_b64": "dGVzdA==",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert "detections" in data
        assert data["frame_id"] == "f1"
