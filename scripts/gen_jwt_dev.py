#!/usr/bin/env python3
"""
Generate a JWT for local development. Requires JWT_SECRET (32+ chars) and PyJWT.
Usage: JWT_SECRET=your_secret python scripts/gen_jwt_dev.py
Output: token string to use as Bearer.
"""
import os
import sys

def main():
    secret = os.getenv("JWT_SECRET")
    if not secret or len(secret) < 32:
        print("Set JWT_SECRET (32+ chars) in env.", file=sys.stderr)
        sys.exit(1)
    try:
        import jwt
    except ImportError:
        print("Install PyJWT: pip install PyJWT", file=sys.stderr)
        sys.exit(1)
    from datetime import datetime, timedelta
    payload = {
        "sub": "dev-user",
        "email": "dev@local",
        "role": os.getenv("JWT_ROLE", "super_admin"),
        "region_ids": [],
        "iss": os.getenv("JWT_ISSUER", "defense-api"),
        "aud": os.getenv("JWT_AUDIENCE", "defense-dashboard"),
        "exp": datetime.utcnow() + timedelta(days=1),
        "iat": datetime.utcnow(),
    }
    token = jwt.encode(
        payload,
        secret,
        algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
    )
    print(token if isinstance(token, str) else token.decode("utf-8"))

if __name__ == "__main__":
    main()
