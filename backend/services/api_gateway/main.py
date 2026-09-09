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
    "mission": os.getenv("MISSION_SERVICE_URL", "http://localhost:8007"),
    "ml": os.getenv("ML_SERVICE_URL", "http://localhost:8011"),
    "drone_bridge": os.getenv("DRONE_BRIDGE_URL", "http://localhost:8010"),
    "simulation": os.getenv("SIMULATION_SERVICE_URL", "http://localhost:8014"),
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


# ---------------------------------------------------------------------------
# Missions and flights (Phase 1): mission planning + human-approved dispatch
# ---------------------------------------------------------------------------

@app.get("/api/v1/missions")
async def list_missions(
    request: Request,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
    site_id: str | None = None,
    org_id: str | None = None,
    active: bool | None = None,
) -> dict:
    params = {"site_id": site_id, "org_id": org_id, "active": active}
    params = {k: v for k, v in params.items() if v is not None}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SERVICE_URLS['mission']}/missions",
            params=params, headers=_proxy_headers(user), timeout=10.0,
        )
        r.raise_for_status()
        return r.json()


@app.post("/api/v1/missions", status_code=201)
async def create_mission(
    body: dict,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SERVICE_URLS['mission']}/missions",
            json=body, headers=_proxy_headers(user), timeout=10.0,
        )
        r.raise_for_status()
        return r.json()


@app.get("/api/v1/missions/{mission_id}")
async def get_mission(
    mission_id: str,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SERVICE_URLS['mission']}/missions/{mission_id}",
            headers=_proxy_headers(user), timeout=10.0,
        )
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Mission not found")
        r.raise_for_status()
        return r.json()


@app.patch("/api/v1/missions/{mission_id}")
async def update_mission(
    mission_id: str, body: dict,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{SERVICE_URLS['mission']}/missions/{mission_id}",
            json=body, headers=_proxy_headers(user), timeout=10.0,
        )
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Mission not found")
        r.raise_for_status()
        return r.json()


@app.put("/api/v1/missions/{mission_id}/waypoints")
async def replace_waypoints(
    mission_id: str, body: list[dict],
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"{SERVICE_URLS['mission']}/missions/{mission_id}/waypoints",
            json=body, headers=_proxy_headers(user), timeout=10.0,
        )
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Mission not found")
        r.raise_for_status()
        return r.json()


@app.get("/api/v1/flights")
async def list_flights(
    request: Request,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
    asset_id: str | None = None,
    mission_id: str | None = None,
    status: str | None = None,
) -> dict:
    params = {"asset_id": asset_id, "mission_id": mission_id, "status": status}
    params = {k: v for k, v in params.items() if v is not None}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SERVICE_URLS['mission']}/flights",
            params=params, headers=_proxy_headers(user), timeout=10.0,
        )
        r.raise_for_status()
        return r.json()


@app.post("/api/v1/flights", status_code=201)
async def create_flight(
    body: dict,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SERVICE_URLS['mission']}/flights",
            json=body, headers=_proxy_headers(user), timeout=10.0,
        )
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail=r.json().get("detail", "Not found"))
        r.raise_for_status()
        return r.json()


@app.post("/api/v1/flights/{flight_id}/approve")
async def approve_flight(
    flight_id: str, body: dict,
    request: Request,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    """The human oversight gate. The approver's identity is recorded."""
    if "approved_by" not in body:
        raise HTTPException(status_code=400, detail="approved_by (user UUID) is required")
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SERVICE_URLS['mission']}/flights/{flight_id}/approve",
            json=body, headers=_proxy_headers(user), timeout=10.0,
        )
        if r.status_code == 409:
            raise HTTPException(status_code=409, detail=r.json().get("detail", "Cannot approve"))
        r.raise_for_status()
        out = r.json()
    audit_log(request, user["id"], "flight_approve", "mission", flight_id)
    return out


@app.post("/api/v1/flights/{flight_id}/start")
async def start_flight(
    flight_id: str, body: dict,
    request: Request,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    """Dispatch an approved flight to the drone bridge."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SERVICE_URLS['mission']}/flights/{flight_id}/start",
            json=body, headers=_proxy_headers(user), timeout=20.0,
        )
        if r.status_code == 403:
            raise HTTPException(status_code=403, detail=r.json().get("detail", "Not approved"))
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Flight not found")
        r.raise_for_status()
        out = r.json()
    audit_log(request, user["id"], "flight_start", "mission", flight_id)
    return out


REPLAY_DIR = os.getenv("REPLAY_DIR", "")


# ---------------------------------------------------------------------------
# Model registry (Phase 2): provenance, evaluation, promotion.
# Approval-equivalent guarantee: /promote records the acting user, mirroring
# the flight approval gate.
# ---------------------------------------------------------------------------

@app.get("/api/v1/models")
async def list_models(
    stage: str | None = None,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    params = {"stage": stage} if stage else None
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SERVICE_URLS['ml']}/models", params=params, timeout=10.0)
        r.raise_for_status()
        return r.json()


@app.post("/api/v1/models", status_code=201)
async def register_model(
    body: dict,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{SERVICE_URLS['ml']}/models", json=body, timeout=10.0)
        if r.status_code == 422:
            raise HTTPException(status_code=422, detail=r.json().get("detail", "Invalid model"))
        r.raise_for_status()
        return r.json()


@app.get("/api/v1/models/production")
async def get_production_model(
    user: dict = Depends(get_current_user),
) -> dict:
    """The model the fleet is certified to run right now (or 404)."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SERVICE_URLS['ml']}/models/production", timeout=5.0)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail=r.json().get("detail", "No production model"))
        r.raise_for_status()
        return r.json()


@app.get("/api/v1/models/{model_id}")
async def get_model(
    model_id: str,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SERVICE_URLS['ml']}/models/{model_id}", timeout=5.0)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Model not found")
        r.raise_for_status()
        return r.json()


@app.get("/api/v1/models/{model_id}/evals")
async def list_model_evals(
    model_id: str,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SERVICE_URLS['ml']}/models/{model_id}/evals", timeout=10.0)
        r.raise_for_status()
        return r.json()


@app.post("/api/v1/models/{model_id}/evals", status_code=201)
async def record_model_eval(
    model_id: str,
    body: dict,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SERVICE_URLS['ml']}/models/{model_id}/evals",
            json=body, timeout=10.0,
        )
        if r.status_code == 422:
            raise HTTPException(status_code=422, detail=r.json().get("detail", "Invalid eval"))
        r.raise_for_status()
        return r.json()


@app.post("/api/v1/models/{model_id}/promote")
async def promote_model(
    model_id: str,
    body: dict,
    request: Request,
    user: dict = Depends(require_role(Role.SUPER_ADMIN)),
) -> dict:
    """The recorded human promotion decision. Gate evaluation runs server-side."""
    if "promoted_by" not in body:
        raise HTTPException(status_code=400, detail="promoted_by (user UUID) is required")
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SERVICE_URLS['ml']}/models/{model_id}/promote",
            json=body, timeout=10.0,
        )
        if r.status_code == 403:
            raise HTTPException(status_code=403, detail=r.json().get("detail", "Gate not satisfied"))
        if r.status_code == 409:
            raise HTTPException(status_code=409, detail=r.json().get("detail", "Promotion refused"))
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Model not found")
        r.raise_for_status()
        out = r.json()
    audit_log(request, user["id"], "model_promote", "ml", model_id)
    return out


# ---------------------------------------------------------------------------
# Drone bridge status (aircraft live state)
# ---------------------------------------------------------------------------

@app.get("/api/v1/drones")
async def list_drones(
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SERVICE_URLS['drone_bridge']}/vehicles", timeout=5.0)
        if r.status_code >= 500:
            raise HTTPException(status_code=502, detail="Drone bridge unreachable")
        r.raise_for_status()
        return r.json()


@app.get("/api/v1/drones/{asset_id}")
async def get_drone_state(
    asset_id: str,
    user: dict = Depends(require_role(Role.SUPER_ADMIN, Role.LOCAL_OPERATOR)),
) -> dict:
    if not validate_asset_id(asset_id):
        raise HTTPException(status_code=400, detail="Invalid asset_id")
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SERVICE_URLS['drone_bridge']}/vehicles/{asset_id}", timeout=5.0)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        r.raise_for_status()
        return r.json()


@app.get("/api/v1/simulation/layers")
async def simulate_layers(user: dict = Depends(get_current_user)) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SERVICE_URLS['simulation']}/layers", timeout=5.0)
        r.raise_for_status()
        return r.json()


@app.get("/api/v1/simulation/exercises")
async def list_exercises(user: dict = Depends(get_current_user)) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SERVICE_URLS['simulation']}/exercises", timeout=8.0)
        r.raise_for_status()
        return r.json()


@app.post("/api/v1/simulation/exercises", status_code=201)
async def create_exercise(body: dict, user: dict = Depends(get_current_user)) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{SERVICE_URLS['simulation']}/exercises", json=body, timeout=15.0)
        r.raise_for_status()
        return r.json()


@app.get("/api/v1/simulation/exercises/{exercise_id}")
async def exercise_status(exercise_id: str, user: dict = Depends(get_current_user)) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{SERVICE_URLS['simulation']}/exercises/{exercise_id}", timeout=60.0)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Exercise not found")
        r.raise_for_status()
        return r.json()


@app.post("/api/v1/simulation/exercises/{exercise_id}/stop")
async def stop_exercise(exercise_id: str, user: dict = Depends(get_current_user)) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{SERVICE_URLS['simulation']}/exercises/{exercise_id}/stop", timeout=10.0)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Exercise not found")
        r.raise_for_status()
        return r.json()


@app.post("/api/v1/simulation/exercises/{exercise_id}/ingest", status_code=201)
async def ingest_exercise_frame(exercise_id: str, body: dict, user: dict = Depends(get_current_user)) -> dict:
    """Layer-3 frame ingest: a rendered 3D-world frame enters the real pipeline."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{SERVICE_URLS['simulation']}/exercises/{exercise_id}/ingest", json=body, timeout=30.0
        )
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Exercise not found")
        r.raise_for_status()
        return r.json()


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
