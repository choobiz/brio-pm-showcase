"""Takeoff page (UI round 1, phase 3): checking several quantities at once, and
the drawing-analysis routes refusing anyone not on the project.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.models.quantity_override import QuantityOverride  # noqa: E402
from app.routers import drawing_analysis as DA  # noqa: E402
from app.routers import quantity_overrides as Q  # noqa: E402


def _project(db):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,'Takeoff',:s,'lead')"),
               {"i": str(pid), "s": f"to-{str(pid)[:8]}"})
    return pid


def _user(db, role="superadmin"):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, :r)"),
               {"i": str(uid), "e": f"{uid}@t.local", "r": role})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)


def _q(db, pid, qt="door_count", value=5):
    row = QuantityOverride(project_id=pid, discipline="architectural", quantity_type=qt,
                           value=value, unit="ea", source="cad_extract", status="candidate")
    db.add(row)
    db.flush()
    return row


def test_bulk_confirm_keeps_the_machine_value_and_records_who(db_session):
    pid, su = _project(db_session), _user(db_session)
    a, b = _q(db_session, pid), _q(db_session, pid, "window_count", 7)
    out = Q.review_overrides_bulk(
        body=Q.BulkReviewWrite(project_id=pid, ids=[a.id, b.id], action="confirm"),
        db=db_session, user=su)
    assert out == {"updated": 2, "status": "confirmed"}
    for r in (a, b):
        assert r.status == "confirmed" and r.machine_value == r.value
        assert str(r.confirmed_by_id) == su.id and r.confirmed_at is not None


def test_bulk_exclude(db_session):
    pid, su = _project(db_session), _user(db_session)
    a = _q(db_session, pid)
    Q.review_overrides_bulk(body=Q.BulkReviewWrite(project_id=pid, ids=[a.id], action="exclude",
                                                   notes="existing work"),
                            db=db_session, user=su)
    assert a.status == "excluded" and "existing work" in a.notes


def test_bulk_refuses_a_quantity_from_another_project(db_session):
    pid, other, su = _project(db_session), _project(db_session), _user(db_session)
    mine, theirs = _q(db_session, pid), _q(db_session, other)
    with pytest.raises(HTTPException) as e:
        Q.review_overrides_bulk(
            body=Q.BulkReviewWrite(project_id=pid, ids=[mine.id, theirs.id], action="confirm"),
            db=db_session, user=su)
    assert e.value.status_code == 404
    assert mine.status == "candidate" and theirs.status == "candidate"


def test_bulk_does_not_correct(db_session):
    pid, su = _project(db_session), _user(db_session)
    a = _q(db_session, pid)
    with pytest.raises(HTTPException) as e:
        Q.review_overrides_bulk(body=Q.BulkReviewWrite(project_id=pid, ids=[a.id], action="correct"),
                                db=db_session, user=su)
    assert e.value.status_code == 422


def test_bulk_needs_project_membership(db_session):
    pid = _project(db_session)
    a = _q(db_session, pid)
    outsider = _user(db_session, role="manager")
    with pytest.raises(HTTPException) as e:
        Q.review_overrides_bulk(body=Q.BulkReviewWrite(project_id=pid, ids=[a.id], action="confirm"),
                                db=db_session, user=outsider)
    assert e.value.status_code == 403


def test_drawing_analysis_refuses_someone_not_on_the_project(db_session):
    pid = _project(db_session)
    client = _user(db_session, role="client")
    with pytest.raises(HTTPException) as e:
        DA.read_analysis(project_id=pid, db=db_session, user=client)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        DA.sheet_view(project_id=pid, document_id=uuid4(), view=None, highlight=None,
                      db=db_session, user=client)
    assert e.value.status_code == 403


def test_drawing_analysis_reads_for_a_member(db_session):
    pid, su = _project(db_session), _user(db_session)
    out = DA.read_analysis(project_id=pid, db=db_session, user=su)
    assert out["pack"] is None and "gate" in out


def test_workbench_routes_refuse_someone_not_on_the_project(db_session):
    """Router-level guard: exercised through the real app, not by calling the
    function, because the dependency only runs in the request pipeline."""
    from fastapi.testclient import TestClient

    from app.auth import get_current_user
    from app.db import get_db
    from app.main import app

    pid = _project(db_session)
    outsider = _user(db_session, role="manager")
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: outsider
    try:
        r = TestClient(app).get(f"/projects/{pid}/workbench/estimate-versions")
        assert r.status_code == 403
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id=str(uuid4()), email="a@t.local", role="superadmin")
        # The test schema has no estimate_versions columns (create_all from a
        # pk-only model), so the handler itself may fail; what matters here is
        # that the guard let a superadmin through.
        r = TestClient(app, raise_server_exceptions=False).get(
            f"/projects/{pid}/workbench/estimate-versions")
        assert r.status_code not in (401, 403, 404)
    finally:
        app.dependency_overrides.clear()
