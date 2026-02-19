"""
Security utilities: JWT, input sanitization, rate-limit keys, security headers, SSRF-safe URLs.
Maps to OWASP Top 10 mitigations (see docs/security/OWASP-TOP10.md).
"""
from __future__ import annotations

import os
import re
from typing import Any

# JWT
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ISSUER = os.getenv("JWT_ISSUER", "defense-api")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "defense-dashboard")
JWT_LEEWAY_SECONDS = 10

# Max lengths for string inputs (A03 Injection / overflow)
MAX_STRING_LEN = 2048
MAX_ID_LEN = 128
MAX_ISSUED_BY_LEN = 256

# Allowed query parameter keys per endpoint (A01 Broken Access Control, A10 SSRF)
ALLOWED_QUERY_ASSETS = frozenset({"region_id", "status", "asset_type", "limit", "offset"})
ALLOWED_QUERY_TELEMETRY = frozenset({"asset_id", "region_id", "from_ts", "to_ts", "limit"})
ALLOWED_QUERY_ALERTS = frozenset({"region_id", "state", "severity", "limit", "offset"})
ALLOWED_QUERY_AUDIT = frozenset({"asset_id", "limit"})

# URL allowlist for inference image_url (A10 SSRF). Empty = disallow all URLs in prod.
INFERENCE_ALLOWED_URL_PREFIXES = [
    p.strip() for p in os.getenv("INFERENCE_ALLOWED_URL_PREFIXES", "http://localhost,http://127.0.0.1").split(",")
    if p.strip()
]


def decode_jwt(token: str) -> dict | None:
    """Decode and verify JWT. Returns payload dict or None if invalid."""
    if not JWT_SECRET or len(JWT_SECRET) < 32:
        return None
    try:
        import jwt
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            leeway=JWT_LEEWAY_SECONDS,
        )
        return payload
    except Exception:
        return None


def sanitize_string(value: Any, max_len: int = MAX_STRING_LEN) -> str:
    """Return a safe string: strip, truncate, no control chars."""
    if value is None:
        return ""
    s = str(value).strip()
    s = "".join(c for c in s if ord(c) >= 32 and ord(c) != 127)
    return s[:max_len]


def sanitize_issued_by(issued_by: Any) -> str:
    """For audit log: no injection, bounded length."""
    return sanitize_string(issued_by, MAX_ISSUED_BY_LEN) or "unknown"


def filter_query_params(params: dict[str, Any], allowed: frozenset[str]) -> dict[str, str]:
    """Allowlist query params and coerce to string (one value per key)."""
    out = {}
    for k, v in params.items():
        if k not in allowed:
            continue
        if isinstance(v, (list, tuple)):
            v = v[0] if v else ""
        out[k] = sanitize_string(str(v), MAX_STRING_LEN)
    return out


def is_url_safe_for_fetch(url: str) -> bool:
    """Allowlist for inference image_url to prevent SSRF."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if len(u) > 2048:
        return False
    if not u.startswith("http://") and not u.startswith("https://"):
        return False
    for prefix in INFERENCE_ALLOWED_URL_PREFIXES:
        if u.lower().startswith(prefix.lower()):
            return True
    return False


def security_headers() -> dict[str, str]:
    """Headers to add to responses (A05 Security Misconfiguration)."""
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }
