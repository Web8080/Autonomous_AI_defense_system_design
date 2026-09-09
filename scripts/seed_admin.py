#!/usr/bin/env python3
"""
Create the first organization, site and admin user.

There is no self-signup: accounts are provisioned. Run once after the stack is up.

  python3 scripts/seed_admin.py --email you@example.com --password '<strong>'
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "services" / "auth_service"))

try:
    import asyncpg  # noqa: E402
except ModuleNotFoundError:
    raise SystemExit(
        "asyncpg is not installed.\n\n"
        "Create the project venv once:\n"
        "    python3 -m venv .venv\n"
        "    .venv/bin/pip install -r scripts/requirements.txt\n\n"
        "Then run this script with it:\n"
        "    .venv/bin/python scripts/seed_admin.py --email you@example.com --password '...'"
    )

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://defense:defense@localhost:5432/defense")


def hash_password(password: str) -> str:
    """Mirrors auth_service.hash_password so seeded users can log in."""
    import hashlib
    try:
        from argon2 import PasswordHasher
        return PasswordHasher().hash(password)
    except ImportError:
        salt = secrets.token_bytes(16)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return f"scrypt${salt.hex()}${dk.hex()}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--org", default="Default Organization")
    ap.add_argument("--org-slug", default="default")
    ap.add_argument("--site", default="Primary Site")
    ap.add_argument("--site-slug", default="primary")
    args = ap.parse_args()

    if len(args.password) < 12:
        print("Password must be at least 12 characters.", file=sys.stderr)
        return 1

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        org_id = await conn.fetchval(
            """
            INSERT INTO orgs.organizations (name, slug)
            VALUES ($1, $2)
            ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            args.org, args.org_slug,
        )
        site_id = await conn.fetchval(
            """
            INSERT INTO orgs.sites (org_id, name, slug, legacy_region_id)
            VALUES ($1, $2, $3, $3)
            ON CONFLICT (org_id, slug) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            org_id, args.site, args.site_slug,
        )
        user_id = await conn.fetchval(
            """
            INSERT INTO auth.users (email, role, org_id, password_hash, full_name)
            VALUES ($1, 'super_admin', $2, $3, 'Administrator')
            ON CONFLICT (email) DO UPDATE
              SET password_hash = EXCLUDED.password_hash,
                  role = 'super_admin',
                  org_id = EXCLUDED.org_id,
                  disabled = FALSE,
                  failed_login_count = 0,
                  locked_until = NULL
            RETURNING id
            """,
            args.email, org_id, hash_password(args.password),
        )
        await conn.execute(
            "INSERT INTO auth.user_sites (user_id, site_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            user_id, site_id,
        )
        print(f"org  {org_id}  ({args.org_slug})")
        print(f"site {site_id}  ({args.site_slug})")
        print(f"user {user_id}  ({args.email}) role=super_admin")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
