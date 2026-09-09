"""
Auth service: the token issuer. Before this existed there was no login endpoint
anywhere in the system, so the only working path was the `dev-token` backdoor
that granted super_admin to any caller. That backdoor is now removed.

Issues short-lived access JWTs (verified by the gateway via defense_shared.security.decode_jwt)
and long-lived, hashed, revocable refresh tokens.
"""
import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from uuid import UUID

import asyncpg
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from defense_shared.schemas import Role
from defense_shared.security import JWT_ALGORITHM, JWT_AUDIENCE, JWT_ISSUER, JWT_SECRET

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense")
ACCESS_TOKEN_TTL_MIN = int(os.getenv("ACCESS_TOKEN_TTL_MIN", "15"))
REFRESH_TOKEN_TTL_DAYS = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "30"))

# Account lockout: blunt but effective against credential stuffing (OWASP A07).
MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "5"))
LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))

pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    return pool


# --------------------------------------------------------------------------
# Password hashing
# --------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Argon2id preferred; scrypt fallback so the service still runs without argon2-cffi."""
    try:
        from argon2 import PasswordHasher
        return PasswordHasher().hash(password)
    except ImportError:
        salt = secrets.token_bytes(16)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if stored.startswith("scrypt$"):
        try:
            _, salt_hex, dk_hex = stored.split("$")
            dk = hashlib.scrypt(
                password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32
            )
            return secrets.compare_digest(dk.hex(), dk_hex)
        except Exception:
            return False
    try:
        from argon2 import PasswordHasher
        from argon2.exceptions import VerificationError, VerifyMismatchError
        try:
            return PasswordHasher().verify(stored, password)
        except (VerifyMismatchError, VerificationError):
            return False
    except ImportError:
        return False


def hash_refresh_token(token: str) -> str:
    """Refresh tokens are high-entropy random, so a plain SHA-256 is sufficient
    and lets us look the row up by hash without a per-row verify."""
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------
# Token minting
# --------------------------------------------------------------------------

def _require_secret() -> None:
    # decode_jwt silently returns None on a short secret, which would make every
    # token we mint unverifiable. Fail loudly at issue time instead.
    if not JWT_SECRET or len(JWT_SECRET) < 32:
        raise HTTPException(status_code=500, detail="JWT_SECRET must be set and at least 32 characters")


def mint_access_token(user: dict, site_ids: list[str]) -> tuple[str, int]:
    _require_secret()
    now = datetime.now(timezone.utc)
    expires_in = ACCESS_TOKEN_TTL_MIN * 60
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "role": user["role"],
        "org_id": str(user["org_id"]) if user.get("org_id") else None,
        # region_ids is the claim the gateway already reads and forwards as
        # X-Region-Ids; keep the name so downstream services are unchanged.
        "region_ids": site_ids,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM), expires_in


async def issue_refresh_token(
    db: asyncpg.Pool, user_id: UUID, user_agent: str | None, ip: str | None
) -> str:
    raw = secrets.token_urlsafe(48)
    await db.execute(
        """
        INSERT INTO auth.refresh_tokens (user_id, token_hash, expires_at, user_agent, ip_addr)
        VALUES ($1, $2, $3, $4, $5::inet)
        """,
        user_id,
        hash_refresh_token(raw),
        datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
        (user_agent or "")[:512],
        ip,
    )
    return raw


async def load_site_ids(db: asyncpg.Pool, user_id: UUID, role: str) -> list[str]:
    """Super admins are unscoped. Everyone else is limited to explicitly granted sites."""
    if role == Role.SUPER_ADMIN.value:
        return []
    rows = await db.fetch(
        """
        SELECT s.legacy_region_id, s.id
        FROM auth.user_sites us
        JOIN orgs.sites s ON s.id = us.site_id
        WHERE us.user_id = $1
        """,
        user_id,
    )
    # Downstream asset filtering still matches on the legacy region_id string.
    return [r["legacy_region_id"] or str(r["id"]) for r in rows][:50]


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    if pool:
        await pool.close()


app = FastAPI(title="Auth Service", lifespan=lifespan)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=16, max_length=512)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "auth-service"}


@app.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    row = await db.fetchrow(
        """
        SELECT id, email, role, org_id, password_hash, disabled, failed_login_count, locked_until
        FROM auth.users WHERE lower(email) = lower($1)
        """,
        body.email,
    )

    # Uniform failure response: never reveal whether the account exists.
    invalid = HTTPException(status_code=401, detail="Invalid credentials")

    if not row:
        # Burn comparable time so absence is not detectable by timing.
        verify_password(body.password, "scrypt$" + "00" * 16 + "$" + "00" * 32)
        raise invalid
    if row["disabled"]:
        raise HTTPException(status_code=403, detail="Account disabled")
    if row["locked_until"] and row["locked_until"] > datetime.now(timezone.utc):
        raise HTTPException(status_code=429, detail="Account temporarily locked")

    if not verify_password(body.password, row["password_hash"]):
        failed = row["failed_login_count"] + 1
        lock_until = (
            datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
            if failed >= MAX_FAILED_LOGINS
            else None
        )
        await db.execute(
            "UPDATE auth.users SET failed_login_count = $2, locked_until = $3 WHERE id = $1",
            row["id"], failed, lock_until,
        )
        raise invalid

    await db.execute(
        "UPDATE auth.users SET failed_login_count = 0, locked_until = NULL, last_login_at = NOW() WHERE id = $1",
        row["id"],
    )

    user = dict(row)
    site_ids = await load_site_ids(db, row["id"], row["role"])
    access, expires_in = mint_access_token(user, site_ids)
    client_ip = request.client.host if request.client else None
    refresh = await issue_refresh_token(db, row["id"], request.headers.get("user-agent"), client_ip)

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": {
            "id": str(row["id"]),
            "email": row["email"],
            "role": row["role"],
            "org_id": str(row["org_id"]) if row["org_id"] else None,
            "site_ids": site_ids,
        },
    }


@app.post("/refresh")
async def refresh_token(
    body: RefreshRequest,
    request: Request,
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    token_hash = hash_refresh_token(body.refresh_token)
    row = await db.fetchrow(
        """
        SELECT rt.id AS rt_id, rt.expires_at, rt.revoked_at,
               u.id, u.email, u.role, u.org_id, u.disabled
        FROM auth.refresh_tokens rt
        JOIN auth.users u ON u.id = rt.user_id
        WHERE rt.token_hash = $1
        """,
        token_hash,
    )
    if not row or row["revoked_at"] or row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    if row["disabled"]:
        raise HTTPException(status_code=403, detail="Account disabled")

    # Rotate: a refresh token is single use, so a stolen one is detectable and short-lived.
    await db.execute("UPDATE auth.refresh_tokens SET revoked_at = NOW() WHERE id = $1", row["rt_id"])

    site_ids = await load_site_ids(db, row["id"], row["role"])
    access, expires_in = mint_access_token(dict(row), site_ids)
    client_ip = request.client.host if request.client else None
    new_refresh = await issue_refresh_token(db, row["id"], request.headers.get("user-agent"), client_ip)

    return {
        "access_token": access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "expires_in": expires_in,
    }


@app.post("/logout")
async def logout(body: RefreshRequest, db: asyncpg.Pool = Depends(get_pool)) -> dict:
    await db.execute(
        "UPDATE auth.refresh_tokens SET revoked_at = NOW() WHERE token_hash = $1 AND revoked_at IS NULL",
        hash_refresh_token(body.refresh_token),
    )
    return {"ok": True}


@app.get("/me")
async def me(
    x_user_id: str | None = Header(None, alias="X-User-Id"),
    db: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Called through the gateway, which has already verified the JWT."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Unauthenticated")
    try:
        user_uuid = UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user id")
    row = await db.fetchrow(
        "SELECT id, email, role, org_id, full_name, last_login_at FROM auth.users WHERE id = $1",
        user_uuid,
    )
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    site_ids = await load_site_ids(db, row["id"], row["role"])
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "role": row["role"],
        "org_id": str(row["org_id"]) if row["org_id"] else None,
        "full_name": row["full_name"],
        "site_ids": site_ids,
        "last_login_at": row["last_login_at"].isoformat() if row["last_login_at"] else None,
    }
