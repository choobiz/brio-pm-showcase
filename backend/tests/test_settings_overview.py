"""Settings page (R2Settings): people for superadmins only, the roles table,
project email addresses. Office only."""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser, require_office  # noqa: E402
from app.routers import settings_overview as router  # noqa: E402


def test_people_only_for_superadmins_and_addresses_listed(db_session):
    pid = uuid4()
    db_session.execute(_T("insert into projects (id, name, slug, status, inbound_token) values (:i,'Example',:s,'in_progress','abc123')"),
                       {"i": str(pid), "s": f"p-{str(pid)[:8]}"})
    boss = CurrentUser(id=str(uuid4()), email="boss@t.local", role="superadmin")
    mgr = CurrentUser(id=str(uuid4()), email="mgr@t.local", role="manager")
    assert router.settings_overview(db_session, boss)["people"] is not None
    out = router.settings_overview(db_session, mgr)
    assert out["people"] is None
    assert any(a["address"].startswith("p-abc123@") for a in out["email"]["project_addresses"])
    assert {r["what"] for r in out["roles"]} >= {"Money, estimates, rates", "AI writing"}
    with pytest.raises(HTTPException):
        require_office(CurrentUser(id=str(uuid4()), email="crew@t.local", role="worker"))
