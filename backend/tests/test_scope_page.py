"""Scope page (UI round 1, phase 4): a described change saved as a new version,
the whole-project PDF using only the latest untagged scope, the files a scope
read, and the scope routes refusing anyone not on the project.
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
from app.models.ai_job import AIJob  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.scope import Scope  # noqa: E402
from app.routers import ai_edits as E  # noqa: E402
from app.routers import scopes as S  # noqa: E402


def _project(db):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,'Scope',:s,'lead')"),
               {"i": str(pid), "s": f"sc-{str(pid)[:8]}"})
    return pid


def _user(db, role="superadmin"):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, :r)"),
               {"i": str(uid), "e": f"{uid}@t.local", "r": role})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)


def _scope(db, pid, version=1, payload=None):
    s = Scope(project_id=pid, version=version,
              payload=payload or {"summary": "Garage", "exclusions": ["Landscaping"]})
    db.add(s)
    db.flush()
    return s


def _edit_job(db, pid, scope_id, changes, instruction="Owner supplies appliances"):
    job = AIJob(project_id=pid, kind="ai_edit", status="done", input={},
                output={"edit_plan": {"record_type": "scope", "record_id": str(scope_id),
                                      "instruction": instruction, "changes": changes}})
    db.add(job)
    db.flush()
    return job


ADD_EXCLUSION = [{"path": "exclusions", "action": "add", "after": "Appliances (owner supplied)"}]


def test_a_described_change_saved_as_a_new_version_leaves_the_old_one(db_session):
    pid, su = _project(db_session), _user(db_session)
    v1 = _scope(db_session, pid)
    job = _edit_job(db_session, pid, v1.id, ADD_EXCLUSION)
    out = E.apply_plan(job_id=job.id, body=E.ApplyRequest(as_new_version=True), db=db_session, user=su)
    assert out["new_version"] and out["record_id"] != str(v1.id)
    db_session.refresh(v1)
    assert v1.payload["exclusions"] == ["Landscaping"]
    v2 = db_session.get(Scope, __import__("uuid").UUID(out["record_id"]))
    assert v2.version == 2
    assert v2.payload["exclusions"] == ["Landscaping", "Appliances (owner supplied)"]
    assert v2.payload["_revision"]["from_version"] == 1
    assert v2.payload["_revision"]["instruction"] == "Owner supplies appliances"


def test_without_the_flag_the_edit_is_in_place_as_before(db_session):
    pid, su = _project(db_session), _user(db_session)
    v1 = _scope(db_session, pid)
    job = _edit_job(db_session, pid, v1.id, ADD_EXCLUSION)
    out = E.apply_plan(job_id=job.id, body=E.ApplyRequest(), db=db_session, user=su)
    assert out["record_id"] == str(v1.id)
    assert db_session.execute(_T("select count(*) from scopes where project_id=:p"),
                              {"p": str(pid)}).scalar() == 1


def test_whole_project_pdf_uses_only_the_latest_untagged_scope(db_session):
    pid = _project(db_session)
    _scope(db_session, pid, 1, {"summary": "first"})
    _scope(db_session, pid, 2, {"summary": "second"})
    _scope(db_session, pid, 3, {"summary": "electrical only", "discipline": "electrical"})
    _, selected, *_ = S._collect_project_scopes(db_session, pid)
    assert sorted(s.payload["summary"] for s in selected) == ["electrical only", "second"]


def test_source_lists_what_was_read_and_what_was_not(db_session):
    pid = _project(db_session)
    pdf = Document(project_id=pid, category="drawing", phase="preconstruction", filename="A1.pdf",
                   r2_key="k/a", mime="application/pdf", size_bytes=1)
    dwg = Document(project_id=pid, category="drawing", phase="preconstruction", filename="A1.dwg",
                   r2_key="k/b", mime="application/acad", size_bytes=1)
    db_session.add_all([pdf, dwg])
    db_session.flush()
    out = S._source_documents(db_session, pid, [str(pdf.id), str(dwg.id), str(uuid4())])
    assert [(d.filename, d.read) for d in out] == [("A1.pdf", True), ("A1.dwg", False), (None, False)]
    assert "can't be read" in out[1].reason and "removed" in out[2].reason


def test_scope_routes_refuse_someone_not_on_the_project(db_session):
    pid = _project(db_session)
    v1 = _scope(db_session, pid)
    client = _user(db_session, role="client")
    with pytest.raises(HTTPException) as e:
        S.list_scopes(project_id=pid, db=db_session, user=client)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        S.get_scope(scope_id=v1.id, db=db_session, user=client)
    assert e.value.status_code == 403


def _estimate_versions_table(db):
    """The test schema maps estimate_versions as a primary key only (no ORM
    model). Add the real columns INSIDE this test's transaction — Postgres DDL
    is transactional, so the rollback removes them again."""
    for col, typ in [("project_id", "uuid"), ("version_number", "int"), ("reference", "text"),
                     ("label", "text"), ("cost_basis", "text"), ("overhead_pct", "numeric"),
                     ("profit_pct", "numeric"), ("notes", "text"), ("payload", "jsonb"),
                     ("parent_version_id", "uuid"), ("is_active", "boolean default false"),
                     ("created_by_id", "uuid"), ("created_at", "timestamptz default now()"),
                     ("ai_job_id", "uuid"), ("scope_id", "uuid")]:
        db.execute(_T(f"ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS {col} {typ}"))


def test_a_described_estimate_change_is_a_new_inactive_version(db_session):
    import json

    _estimate_versions_table(db_session)
    pid, su = _project(db_session), _user(db_session)
    vid = uuid4()
    payload = {"categories": [{"name": "Heating", "contingencyPct": 0,
                               "items": [{"name": "Baseboards", "labor": 1000, "materials": 500}]}]}
    db_session.execute(_T("""
        INSERT INTO estimate_versions (id, project_id, version_number, reference, cost_basis,
            overhead_pct, profit_pct, payload, is_active)
        VALUES (:i, :p, 5, 'EST-1', 'LOW', 5, 8, CAST(:b AS jsonb), true)"""),
        {"i": str(vid), "p": str(pid), "b": json.dumps(payload)})
    job = AIJob(project_id=pid, kind="ai_edit", status="done", input={},
                output={"edit_plan": {"record_type": "estimate_version", "record_id": str(vid),
                                      "instruction": "Swap baseboards for a heat pump",
                                      "changes": [{"path": "categories[0].items[0].labor",
                                                   "action": "replace", "before": 1000, "after": 4000}]}})
    db_session.add(job)
    db_session.flush()
    out = E.apply_plan(job_id=job.id, body=E.ApplyRequest(as_new_version=True), db=db_session, user=su)
    row = db_session.execute(_T("SELECT version_number, is_active, parent_version_id, payload "
                                "FROM estimate_versions WHERE id = :i"), {"i": out["record_id"]}).one()
    assert (row.version_number, row.is_active, str(row.parent_version_id)) == (6, False, str(vid))
    cat = row.payload["categories"][0]
    # Re-rolled with the declared 0% — no contingency re-added by the edit.
    assert cat["total"] == 4500 and cat["contingencyAmount"] == 0
    assert row.payload["_revision"]["from_version"] == 5
    orig = db_session.execute(_T("SELECT is_active, payload FROM estimate_versions WHERE id = :i"),
                              {"i": str(vid)}).one()
    assert orig.is_active and orig.payload["categories"][0]["items"][0]["labor"] == 1000


def test_an_edited_version_is_dated_today_and_its_header_names_the_project(db_session):
    """Rollout test 2026-10-14: v2, saved from a described change, kept v1's
    (invented) date and its header read "—" instead of the project name and
    address, because both came from the AI job that only v1 had."""
    from app.services.home import today

    pid = _project(db_session)
    db_session.execute(_T("update projects set address = '123 Test St' where id = :i"), {"i": str(pid)})
    v1 = _scope(db_session, pid, payload={"summary": "Garage", "date": "2025-01-16", "exclusions": []})
    su = _user(db_session)
    job = _edit_job(db_session, pid, v1.id, ADD_EXCLUSION)
    out = E.apply_plan(job_id=job.id, body=E.ApplyRequest(as_new_version=True), db=db_session, user=su)
    from uuid import UUID

    v2 = db_session.get(Scope, UUID(str(out["record_id"])))
    assert v2.payload["date"] == today().isoformat()
    read = S._to_read(db_session, v2)
    assert read.source is None
    assert read.project["name"] == "Scope" and read.project["address"] == "123 Test St"
