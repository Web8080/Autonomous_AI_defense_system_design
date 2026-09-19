"""Phase 5 tenancy helper tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))

from defense_shared.tenancy import (  # noqa: E402
    TenancyError,
    filter_sites,
    require_site_scope,
)
import pytest


def test_super_admin_bypasses_site_list():
    require_site_scope({"roles": ["super_admin"]}, "site-x")


def test_operator_must_match_site():
    with pytest.raises(TenancyError):
        require_site_scope({"roles": ["local_operator"], "site_ids": ["a"]}, "b")
    require_site_scope({"roles": ["local_operator"], "site_ids": ["a", "b"]}, "b")


def test_filter_sites():
    claims = {"roles": ["local_operator"], "site_ids": ["1", "2"]}
    assert filter_sites(claims, ["1", "3", "2"]) == ["1", "2"]
