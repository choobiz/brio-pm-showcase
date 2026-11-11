"""Clients, Sales (CRM), Skills and Reviews are superadmin-only (Amir, 2026-10-15).

Found in the rollout test as the test manager: /clients returned all 100
contacts and /crm/queue 100 opportunities. Every route in these routers checked
only that someone was signed in, so crew and client (owner) accounts could read
them too.
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.main import app

LOCKED = [("get", "/clients"), ("get", "/crm/queue"), ("get", "/crm/contacts"), ("get", "/reviews"),
          ("get", "/reviews/stats"), ("get", "/skills"), ("get", "/skill-runs")]


@pytest.fixture
def as_role(db_session):
    def _as(role):
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id=str(uuid4()), email=f"{role}@t.local", role=role)
        return TestClient(app, raise_server_exceptions=False)
    yield _as
    app.dependency_overrides.clear()


@pytest.mark.parametrize("role", ["manager", "worker", "owner"])
@pytest.mark.parametrize("method,path", LOCKED)
def test_only_a_superadmin_reaches_it(as_role, role, method, path):
    assert getattr(as_role(role), method)(path).status_code == 403


@pytest.mark.parametrize("method,path", LOCKED)
def test_a_superadmin_still_does(as_role, method, path):
    assert getattr(as_role("superadmin"), method)(path).status_code not in (401, 403)


def test_a_manager_can_still_judge_an_ai_output(as_role):
    """The feedback routes stay open to the office; only the listings closed."""
    r = as_role("manager").get("/skill-runs/by-artifact/x/y/z")
    assert r.status_code not in (401, 403)
    assert as_role("worker").get("/skill-runs/by-artifact/x/y/z").status_code == 403
