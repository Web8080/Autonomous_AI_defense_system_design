"""
API Gateway: auth, RBAC, routing, and security (OWASP-aligned).
All client traffic goes through here. JWT required for protected routes.
"""
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx

from defense_shared.schemas import CommandIntent, Role
from defense_shared.security import (
    decode_jwt,
    filter_query_params,
    sanitize_issued_by,
    ALLOWED_QUERY_ALERTS,
    ALLOWED_QUERY_ASSETS,
    ALLOWED_QUERY_TELEMETRY,
)
from defense_shared.safeguardrails import (
    is_allowed_intent,
    validate_asset_id,
    validate_command_payload,
)
from middleware import SecurityHeadersMiddleware, RateLimitMiddleware, audit_log

security = HTTPBearer(auto_error=False)

SERVICE_URLS = {
    "asset": os.getenv("ASSET_SERVICE_URL", "http://localhost:8001"),
    "telemetry": os.getenv("TELEMETRY_SERVICE_URL", "http://localhost:8002"),
    "alert": os.getenv("ALERT_SERVICE_URL", "http://localhost:8003"),
    "control": os.getenv("CONTROL_SERVICE_URL", "http://localhost:8004"),
    "inference": os.getenv("INFERENCE_SERVICE_URL", "http://localhost:8005"),
}


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """Validate JWT and return user identity. 401 if missing or invalid."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = credentials.credentials
    payload = decode_jwt(token)
    if payload:
        sub = payload.get("sub")
        role = payload.get("role", Role.LOCAL_OPERATOR.value)
        region_ids = payload.get("region_ids") or []
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        return {"id": sanitize_issued_by(sub), "email": payload.get("email", ""), "role": role, "region_ids": list(region_ids)[:50]}
    if os.getenv("ALLOW_DEV_TOKEN") == "true" and token == "dev-token":
        return {"id": "dev-user", "email": "dev@local", "role": Role.SUPER_ADMIN.value, "region_ids": []}
    raise HTTPException(status_code=401, detail="Invalid or expired token")


def require_role(*allowed: Role):
    def checker(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in [r.value for r in allowed]:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return checker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield


app = FastAPI(title="Defense API Gateway", lifespan=lifespan)

origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "api-gateway"}


def _proxy_headers(user: dict) -> dict[str, str]:
    return {"X-User-Id": user["id"], "X-User-Role": user["role"], "X-Region-Ids": ",".join(user.get("region_ids") or [])}


@app.get("/api/v1/assets")
async def list_assets(
    request: Request,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    params = filter_query_params(dict(request.query_params), ALLOWED_QUERY_ASSETS)
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SERVICE_URLS['asset']}/assets",
            params=params,
            headers=_proxy_headers(user),
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()


@app.get("/api/v1/telemetry/aggregated")
async def get_telemetry(
    request: Request,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    params = filter_query_params(dict(request.query_params), ALLOWED_QUERY_TELEMETRY)
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SERVICE_URLS['telemetry']}/aggregated",
            params=params,
            headers=_proxy_headers(user),
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()


@app.get("/api/v1/alerts")
async def list_alerts(
    request: Request,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    params = filter_query_params(dict(request.query_params), ALLOWED_QUERY_ALERTS)
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SERVICE_URLS['alert']}/alerts",
            params=params,
            headers=_proxy_headers(user),
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()


def _validate_emergency_stop_body(body: Any) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be JSON object")
    asset_id = body.get("asset_id")
    if asset_id is not None:
        asset_id = str(asset_id).strip()
        if asset_id and not validate_asset_id(asset_id):
            raise HTTPException(status_code=400, detail="Invalid asset_id")
    return {"asset_id": asset_id or "all"}


@app.post("/api/v1/control/emergency-stop")
async def emergency_stop(
    request: Request,
    body: dict,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    validated = _validate_emergency_stop_body(body)
    payload_to_control = {**validated, "issued_by": user["id"]}
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SERVICE_URLS['control']}/emergency-stop",
            json=payload_to_control,
            headers=_proxy_headers(user),
            timeout=5.0,
        )
        r.raise_for_status()
        out = r.json()
    audit_log(request, user["id"], "emergency_stop", "control", validated.get("asset_id", "all"))
    return out


def _validate_command_body(body: Any, user: dict) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be JSON object")
    asset_id = body.get("asset_id")
    intent = body.get("intent")
    if not asset_id or not validate_asset_id(asset_id):
        raise HTTPException(status_code=400, detail="Valid asset_id required")
    if not intent or not is_allowed_intent(str(intent)):
        raise HTTPException(status_code=400, detail="Invalid or disallowed intent")
    payload = body.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be object")
    ok, err = validate_command_payload(payload or {})
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    return {
        "asset_id": str(asset_id).strip(),
        "intent": str(intent).strip().lower(),
        "payload": payload or {},
        "issued_by": user["id"],
        "is_override": bool(body.get("is_override", False)),
    }


@app.post("/api/v1/control/command")
async def send_command(
    request: Request,
    body: dict,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    payload_to_control = _validate_command_body(body, user)
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SERVICE_URLS['control']}/command",
            json=payload_to_control,
            headers=_proxy_headers(user),
            timeout=10.0,
        )
        r.raise_for_status()
        out = r.json()
    audit_log(request, user["id"], "command", "control", payload_to_control.get("intent", ""))
    return out


@app.get("/api/v1/inference/health")
async def inference_health(
    user: dict = Depends(get_current_user),
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SERVICE_URLS['inference']}/health", timeout=5.0)
        r.raise_for_status()
        return r.json()
