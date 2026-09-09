"""
Gateway auth tests.

The previous version of this file asserted that a garbage Bearer token yielded
200/502/503 — it encoded the dev-token vulnerability as expected behaviour.
An unverifiable token must be rejected with 401.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

TEST_SECRET = "test_secret_that_is_at_least_32_chars_long!!"
os.environ["JWT_SECRET"] = TEST_SECRET
os.environ["JWT_ISSUER"] = "defense-api"
os.environ["JWT_AUDIENCE"] = "defense-dashboard"

from conftest import load_service_app  # noqa: E402

app = load_service_app("api_gateway")


def make_token(
    role: str = "super_admin",
    sub: str = "11111111-1111-1111-1111-111111111111",
    expires_in: int = 900,
    issuer: str = "defense-api",
    audience: str = "defense-dashboard",
    secret: str = TEST_SECRET,
) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": sub,
            "email": "test@example.com",
            "role": role,
            "region_ids": [],
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
        },
        secret,
        algorithm="HS256",
    )


def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_health_is_public():
    async with client() as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "api-gateway"


@pytest.mark.asyncio
async def test_assets_requires_auth():
    async with client() as c:
        r = await c.get("/api/v1/assets")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unsigned_garbage_token_rejected():
    """Regression: 'stub-token' style values must never authenticate."""
    async with client() as c:
        r = await c.get("/api/v1/assets", headers={"Authorization": "Bearer stub-token"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_dev_token_backdoor_is_gone():
    """Regression: the removed ALLOW_DEV_TOKEN path granted super_admin to anyone."""
    os.environ["ALLOW_DEV_TOKEN"] = "true"
    try:
        async with client() as c:
            r = await c.get("/api/v1/assets", headers={"Authorization": "Bearer dev-token"})
        assert r.status_code == 401
    finally:
        os.environ.pop("ALLOW_DEV_TOKEN", None)


@pytest.mark.asyncio
async def test_token_signed_with_wrong_secret_rejected():
    token = make_token(secret="a_different_secret_also_32_chars_long_xx")
    async with client() as c:
        r = await c.get("/api/v1/assets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_expired_token_rejected():
    token = make_token(expires_in=-3600)
    async with client() as c:
        r = await c.get("/api/v1/assets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_wrong_issuer_rejected():
    token = make_token(issuer="someone-else")
    async with client() as c:
        r = await c.get("/api/v1/assets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_wrong_audience_rejected():
    token = make_token(audience="another-app")
    async with client() as c:
        r = await c.get("/api/v1/assets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_alg_none_rejected():
    """Classic JWT bypass: unsigned token with alg=none."""
    token = jwt.encode({"sub": "x", "role": "super_admin"}, key="", algorithm="none")
    async with client() as c:
        r = await c.get("/api/v1/assets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_valid_token_passes_auth_layer():
    """A correctly signed token must clear auth and RBAC and reach the proxy step.

    What happens next depends on whether the asset service is running: a real
    response if it is, a ConnectError if it is not. Either outcome proves the
    request got past auth, which is what this test is about.
    """
    import httpx

    token = make_token()
    try:
        async with client() as c:
            r = await c.get("/api/v1/assets", headers={"Authorization": f"Bearer {token}"})
    except httpx.ConnectError:
        return  # Reached the proxy with no backend listening.
    assert r.status_code not in (401, 403)


@pytest.mark.asyncio
async def test_unknown_role_is_forbidden():
    token = make_token(role="janitor")
    async with client() as c:
        r = await c.get("/api/v1/assets", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
