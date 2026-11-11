"""A crew (worker) account reaches no money, estimate or office documents.

Rollout part 3 (2026-10-15), signed in as the test crew member on ZZ: the field
pages showed nothing of the kind, but the API answered with the money summary
(contract, owing), every estimate version with costs and margins, invoices, all
AI jobs and their outputs, the company labour and material rates and the cost
analytics. A client (owner) account reached the same through the proxy.
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as _T

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.main import app


@pytest.fixture
def setup(db_session):
    pid = uuid4()
    db_session.execute(_T("insert into projects (id, name, slug, status) values (:i,'ZZ',:s,'in_progress')"),
                       {"i": str(pid), "s": f"cr-{pid.hex[:8]}"})
    users = {}
    for role in ("worker", "owner", "manager"):
        uid = uuid4()
        db_session.execute(_T("insert into users (id, email, role) values (:i,:e,:r)"),
                           {"i": str(uid), "e": f"{uid}@t.local", "r": role})
        db_session.execute(_T("insert into project_members (project_id, user_id) values (:p,:u)"),
                           {"p": str(pid), "u": str(uid)})
        users[role] = CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)

    def client(role):
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: users[role]
        return TestClient(app, raise_server_exceptions=False)
    yield pid, client
    app.dependency_overrides.clear()


def _money_paths(pid):
    return [f"/projects/{pid}/money", f"/projects/{pid}/workbench/estimate-versions",
            f"/invoices?project_id={pid}", "/ai-jobs", f"/ai-jobs?project_id={pid}", "/rates/labour",
            "/rates/materials", "/analytics/costs", "/divisions", "/register", f"/line-rates/estimate-version/{uuid4()}",
            f"/documents?project_id={pid}", f"/documents/search?q=contract&project_id={pid}",
            f"/documents/index-status?project_id={pid}"]


@pytest.mark.parametrize("role", ["worker", "owner"])
def test_crew_and_client_accounts_get_no_money(setup, role):
    pid, client = setup
    c = client(role)
    leaks = [p for p in _money_paths(pid) if c.get(p).status_code != 403]
    assert leaks == [], f"{role} reached: {leaks}"


def test_a_manager_on_the_project_still_does(setup):
    pid, client = setup
    c = client("manager")
    blocked = [p for p in _money_paths(pid) if c.get(p).status_code in (401, 403)]
    assert blocked == [], blocked


def test_crew_can_still_upload_a_site_photo(setup):
    pid, client = setup
    r = client("worker").post("/documents/presign", json={"project_id": str(pid), "filename": "site.jpg",
                                                          "content_type": "image/jpeg", "category": "photo"})
    assert r.status_code not in (401, 403), r.text


def test_crew_get_the_project_without_the_drive_link_or_talking_points(setup, db_session):
    pid, client = setup
    db_session.execute(_T("update projects set google_drive_url='https://drive.example/folder' where id=:p"),
                       {"p": str(pid)})
    for role in ("worker", "owner"):
        c = client(role)
        assert c.get(f"/projects/{pid}").json()["google_drive_url"] is None
        assert all(p["google_drive_url"] is None for p in c.get("/projects").json())
    assert client("manager").get(f"/projects/{pid}").json()["google_drive_url"] == "https://drive.example/folder"
