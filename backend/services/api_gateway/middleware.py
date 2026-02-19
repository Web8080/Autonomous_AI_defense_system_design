"""
Security middleware: rate limit, security headers, audit logging for sensitive paths.
"""
import time
from collections import defaultdict
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from defense_shared.security import security_headers

# In-memory rate limit: key -> (count, window_start). Use Redis in production.
_rate_store: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 120
RATE_LIMIT_AUTH_MAX = 10


def _rate_limit_key(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        for k, v in security_headers().items():
            response.headers[k] = v
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, window: int = RATE_LIMIT_WINDOW, max_requests: int = RATE_LIMIT_MAX):
        super().__init__(app)
        self.window = window
        self.max_requests = max_requests

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        key = _rate_limit_key(request)
        path = request.url.path
        is_auth = path.endswith("/login") or "login" in path
        limit = RATE_LIMIT_AUTH_MAX if is_auth else self.max_requests
        now = time.time()
        count, start = _rate_store[key]
        if now - start > self.window:
            count, start = 0, now
        count += 1
        _rate_store[key] = (count, start)
        if count > limit:
            return Response(status_code=429, content="Too Many Requests")
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
        return response


def audit_log(request: Request, user_id: str, action: str, resource: str, detail: str = "") -> None:
    """Write to structured log only (no PII in logs). Use for sensitive actions."""
    import logging
    log = logging.getLogger("defense.audit")
    log.info(
        "audit action=%s resource=%s user_id=%s detail=%s path=%s",
        action, resource, user_id, detail[:200], request.url.path,
    )
