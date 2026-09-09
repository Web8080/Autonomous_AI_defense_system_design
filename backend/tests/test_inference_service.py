import pytest
from httpx import ASGITransport, AsyncClient

import sys
import os
from pathlib import Path
from conftest import load_service_app

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

app = load_service_app("inference_service")


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


@pytest.mark.asyncio
async def test_infer_stub_is_inert_not_alertable():
    """Stub detections must not reach operators as real threats: label them,
    zero the score, and mark provenance so consumers discard them."""
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
        det = r.json()["detections"][0]
        assert det["class_name"] == "stub_threat"
        assert det["threat_score"] == 0.0
        assert det["model_version"] == "stub"
        assert det["metadata"]["provenance"] == "stub"


def test_model_stamp_without_registry_is_unregistered():
    import services.inference_service.main as mod
    mod._model_meta = None
    stamp = mod._model_stamp()
    assert stamp["model_version"] == "unregistered"
    assert stamp["model_id"] is None
