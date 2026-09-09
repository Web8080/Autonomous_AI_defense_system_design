"""
API Gateway: auth, RBAC, routing, and security (OWASP-aligned).
All client traffic goes through here. JWT required for protected routes.
"""
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
    "auth": os.getenv("AUTH_SERVICE_URL", "http://localhost:8006"),
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
    # The former ALLOW_DEV_TOKEN / "dev-token" escape hatch granted super_admin
    # to any caller and has been removed. Obtain a real token from auth-service.
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
# Order matters: add_middleware prepends, so the last one added is the outermost.
# CORS is added last so that rate-limit 429s and upstream-failure 502s still
# carry Access-Control-Allow-Origin. Without this the browser surfaces an opaque
# CORS error and the real status code is invisible to the operator.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(httpx.TimeoutException)
async def _upstream_timeout(request: Request, exc: httpx.TimeoutException) -> JSONResponse:
    return JSONResponse(status_code=504, content={"detail": "Upstream service timed out"})


@app.exception_handler(httpx.RequestError)
async def _upstream_unreachable(request: Request, exc: httpx.RequestError) -> JSONResponse:
    """A downstream service is down. Previously this escaped as an unhandled 500
    with no CORS headers, so the dashboard could only report 'Failed to fetch'."""
    return JSONResponse(status_code=502, content={"detail": "Upstream service unavailable"})


@app.exception_handler(httpx.HTTPStatusError)
async def _upstream_status(request: Request, exc: httpx.HTTPStatusError) -> JSONResponse:
    status = exc.response.status_code
    return JSONResponse(
        status_code=status if 400 <= status < 600 else 502,
        content={"detail": "Upstream service error"},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "api-gateway"}


def _proxy_headers(user: dict) -> dict[str, str]:
    return {"X-User-Id": user["id"], "X-User-Role": user["role"], "X-Region-Ids": ",".join(user.get("region_ids") or [])}


@app.post("/api/v1/auth/login")
async def auth_login(body: dict, request: Request) -> dict:
    """Unauthenticated by design. Rate limited by RateLimitMiddleware (path contains 'login')."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SERVICE_URLS['auth']}/login",
            json=body,
            headers={"user-agent": request.headers.get("user-agent", "")},
            timeout=10.0,
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.json().get("detail", "Login failed"))
    return r.json()


@app.post("/api/v1/auth/refresh")
async def auth_refresh(body: dict, request: Request) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SERVICE_URLS['auth']}/refresh",
            json=body,
            headers={"user-agent": request.headers.get("user-agent", "")},
            timeout=10.0,
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.json().get("detail", "Refresh failed"))
    return r.json()


@app.post("/api/v1/auth/logout")
async def auth_logout(body: dict) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{SERVICE_URLS['auth']}/logout", json=body, timeout=10.0)
    return r.json() if r.status_code < 400 else {"ok": True}


@app.get("/api/v1/auth/me")
async def auth_me(user: dict = Depends(get_current_user)) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SERVICE_URLS['auth']}/me", headers=_proxy_headers(user), timeout=10.0
        )
        r.raise_for_status()
        return r.json()


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


REPLAY_DIR = os.getenv("REPLAY_DIR", "")


@app.get("/api/v1/simulation/replay")
async def get_simulation_replay(
    name: str = "",
    user: dict = Depends(get_current_user),
) -> dict:
    """Serve a scenario replay JSON by name (e.g. railway_line_replay.json). Set REPLAY_DIR on gateway."""
    if not REPLAY_DIR or not name:
        raise HTTPException(status_code=404, detail="Replay not found")
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="Invalid name")
    path = os.path.join(REPLAY_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Replay not found")
    try:
        with open(path) as f:
            import json
            return json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load replay")
