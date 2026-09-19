"""Org / site tenancy helpers for Phase 5.

Author: Victor.I

Claims expected on the JWT (auth-service): `org_id`, `site_ids` (list).
Gateway and services call these helpers before mutating tenant-scoped rows.
"""
from __future__ import annotations

from typing import Any, Iterable


class TenancyError(PermissionError):
    """Caller is not allowed to touch this org/site."""


def site_ids_from_claims(claims: dict[str, Any]) -> set[str]:
    raw = claims.get("site_ids") or claims.get("sites") or []
    if isinstance(raw, str):
        return {raw}
    return {str(s) for s in raw if s}


def org_id_from_claims(claims: dict[str, Any]) -> str | None:
    v = claims.get("org_id") or claims.get("organisation_id")
    return str(v) if v else None


def require_site_scope(claims: dict[str, Any], site_id: str, *, role: str | None = None) -> None:
    """Raise TenancyError if the token cannot access site_id.

    super_admin bypasses site list (still must be authenticated).
    """
    roles = claims.get("roles") or claims.get("role")
    role_set = {roles} if isinstance(roles, str) else set(roles or [])
    if role:
        role_set.add(role)
    if "super_admin" in role_set:
        return
    allowed = site_ids_from_claims(claims)
    if not allowed:
        raise TenancyError("token has no site_ids claim")
    if str(site_id) not in allowed:
        raise TenancyError(f"site {site_id!r} not in token scope")


def filter_sites(claims: dict[str, Any], candidates: Iterable[str]) -> list[str]:
    roles = claims.get("roles") or claims.get("role")
    role_set = {roles} if isinstance(roles, str) else set(roles or [])
    if "super_admin" in role_set:
        return [str(s) for s in candidates]
    allowed = site_ids_from_claims(claims)
    return [str(s) for s in candidates if str(s) in allowed]
