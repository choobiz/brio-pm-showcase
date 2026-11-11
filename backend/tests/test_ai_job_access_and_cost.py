"""AI writing for managers, with a cost approved first (Amir, 2026-10-15).

Until that day `POST /ai-jobs` was superadmin-only, so a manager pressing Write a
new scope, Generate estimate or Propose changes got "forbidden"; none of the
three showed a cost first; the full estimate recorded $0 for every run; and a
job repaired after unparseable output recorded only its last call.
"""
from __future__ import annotations

import inspect
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as _T

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.main import app
from app.models.ai_job import AIJob
from app.services import cost_estimate
from app.services.ai import AnthropicResult
from app.workers import ai_worker as W


def _project(db):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i, 'ZZ', :s, 'lead')"),
               {"i": str(pid), "s": f"aj-{pid.hex[:8]}"})
    return pid


def _user(db, role, project_id=None):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, :r)"),
               {"i": str(uid), "e": f"{uid}@t.local", "r": role})
    if project_id:
        db.execute(_T("insert into project_members (project_id, user_id) values (:p, :u)"),
                   {"p": str(project_id), "u": str(uid)})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)


@pytest.fixture
def as_user(db_session):
    def _as(user):
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)
    yield _as
    app.dependency_overrides.clear()


def _post(client, pid, kind, **inp):
    return client.post("/ai-jobs", json={"project_id": str(pid), "kind": kind, "input": inp})


def test_a_manager_on_the_project_can_start_ai_writing(db_session, as_user):
    pid = _project(db_session)
    r = _post(as_user(_user(db_session, "manager", pid)), pid, "scope_analysis",
              notes="Bathroom", approve_usd=0.5)
    assert r.status_code == 201, r.text


def test_a_worker_or_a_manager_off_the_project_cannot(db_session, as_user):
    pid = _project(db_session)
    assert _post(as_user(_user(db_session, "worker", pid)), pid, "scope_analysis",
                 approve_usd=0.5).status_code == 403
    assert _post(as_user(_user(db_session, "manager")), pid, "scope_analysis",
                 approve_usd=0.5).status_code == 403


@pytest.mark.parametrize("kind", ["scope_analysis", "estimate_full", "ai_edit"])
def test_scope_estimate_and_describe_a_change_need_an_approved_cost(db_session, as_user, kind):
    pid = _project(db_session)
    c = as_user(_user(db_session, "superadmin"))
    for bad in ({}, {"approve_usd": 0}, {"approve_usd": 500}, {"approve_usd": True}):
        r = _post(c, pid, kind, **bad)
        assert r.status_code == 422 and "Approve the cost first" in r.text, (bad, r.text)
    assert _post(c, pid, kind, approve_usd=1.25).status_code == 201


def test_other_kinds_still_run_without_one(db_session, as_user):
    pid = _project(db_session)
    r = _post(as_user(_user(db_session, "superadmin")), pid, "daily_report_capture", transcript="x")
    assert r.status_code == 201, r.text


def test_the_cost_shown_comes_from_jobs_on_the_current_model(db_session, as_user):
    pid = _project(db_session)
    c = as_user(_user(db_session, "manager", pid))
    first = c.get(f"/ai-jobs/cost-estimate?project_id={pid}&kind=scope_analysis").json()
    assert first["usd"] == 0.5 and "default" in first["how"]
    # Three dear runs on the old model must not raise the figure for the new one.
    for model, usd in [("claude-opus-4-7", "0.90")] * 3 + [("claude-opus-5-5", "0.10")] * 3:
        db_session.add(AIJob(project_id=pid, kind="scope_analysis", status="done", input={},
                             model=model, cost_usd=Decimal(usd)))
    db_session.flush()
    out = cost_estimate.ai_job(db_session, "scope_analysis")
    assert out["usd"] == 0.15 and "median of the last 3" in out["how"]


def _result(text, usd):
    return AnthropicResult(text=text, input_tokens=100, output_tokens=50,
                           cost_usd=Decimal(usd), model="m")


def test_a_repaired_job_records_every_call(monkeypatch):
    monkeypatch.setattr(W, "SELF_CORRECT_KINDS", {"scope_analysis"})
    calls = iter([_result("not json", "0.10"), _result('{"ok": 1}', "0.12")])
    monkeypatch.setattr(W, "_call_with_retry", lambda **_: next(calls))
    out = W._call_self_correcting(kind="scope_analysis", model="m", system=None,
                                  messages=[{"role": "user", "content": "x"}], max_tokens=100)
    assert out.text == '{"ok": 1}'
    assert out.cost_usd == Decimal("0.22") and out.input_tokens == 200 and out.output_tokens == 100


def test_no_repair_starts_once_the_approved_amount_is_spent(monkeypatch):
    monkeypatch.setattr(W, "SELF_CORRECT_KINDS", {"scope_analysis"})
    seen = []

    def _call(**_):
        seen.append(1)
        return _result("not json", "0.30")
    monkeypatch.setattr(W, "_call_with_retry", _call)
    out = W._call_self_correcting(kind="scope_analysis", model="m", system=None,
                                  messages=[{"role": "user", "content": "x"}], max_tokens=100,
                                  approved_usd=0.25)
    assert len(seen) == 1 and out.cost_usd == Decimal("0.30")


def test_the_full_estimate_records_its_cost_and_stops_at_the_approved_amount(db_session, monkeypatch):
    pid = _project(db_session)
    job = AIJob(project_id=pid, kind="estimate_full", status="running", input={})
    db_session.add(job)
    db_session.flush()
    for name in ("plan", "begin", "finish"):
        monkeypatch.setattr(W._progress, name, lambda *a, **k: None)
    import app.services.estimate_readiness as ER

    monkeypatch.setattr(ER, "compute_readiness", lambda db, p: {"takeoff_mode": "none", "confidence": "low"})
    monkeypatch.setattr(W, "_contingency_for_project", lambda db, p: None)
    monkeypatch.setattr(W, "_merge_estimate_inputs", lambda db, p, i: dict(i))
    monkeypatch.setattr(W, "build_messages_for_job", lambda kind, inp: (None, [], 100))
    # The first trade prices fine and spends past the approval; the second must
    # then not start. (A trade that cannot be priced now fails the run on its
    # own, see test_estimate_full_discipline_failure.py, so it cannot be the
    # thing that spends the budget here.)
    monkeypatch.setattr(W, "_call_with_retry", lambda **_: _result('{"categories": []}', "0.04"))
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr(db_session, "rollback", lambda: None)

    W._run_estimate_full(db_session, job, {"scope_id": "s", "method": "scope", "approve_usd": 0.03,
                                           "disciplines": ["architectural", "structural"]})
    assert job.status == "error" and "you approved" in job.error and "Nothing was saved" in job.error
    # One call on the first trade ($0.04 >= $0.03), none on the second.
    assert job.cost_usd == Decimal("0.04") and job.input_tokens == 100
    assert job.model == "claude-sonnet-5-5"


def test_the_full_estimate_is_dated_in_vancouver():
    """At 5 pm or later in Vancouver, the UTC date is already tomorrow."""
    src = inspect.getsource(W._run_estimate_full)
    assert 'datetime.now(timezone.utc).strftime("%Y-%m-%d")' not in src
    assert "_vancouver_today().isoformat()" in src


def test_a_persons_projects_can_be_changed_after_the_invite(db_session, as_user):
    """Users & Roles now edits assignments; the API replaces the whole list, and
    only a superadmin may call it (a manager is refused, test m5)."""
    a, b = _project(db_session), _project(db_session)
    person = _user(db_session, "manager", a)
    r = as_user(_user(db_session, "superadmin")).patch(f"/users/{person.id}", json={"project_ids": [str(b)]})
    assert r.status_code == 200 and r.json()["project_ids"] == [str(b)]
    r = as_user(_user(db_session, "manager", b)).patch(f"/users/{person.id}", json={"project_ids": [str(a)]})
    assert r.status_code == 403
