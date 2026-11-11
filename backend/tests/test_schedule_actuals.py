"""Actual dates per schedule activity: read from site reports, typed facts on top.

Real shapes from 1234 (prod, 2026-10-29): footings saved "delayed, 0%, will be
poured tomorrow" on a DRAFT report. That is not a start, and drafts count.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.models.daily_site_report import DailySiteReport  # noqa: E402
from app.routers.schedule_actuals import ActualIn, list_actuals, record_actual  # noqa: E402
from app.services.actuals import derive_from_reports  # noqa: E402

D = date


def _task(code, status, pct=0, notes=None, removed=False):
    return {"activity_code": code, "status": status, "percent_complete": pct,
            "notes": notes, "removed": removed}


def test_delayed_at_zero_is_not_a_start_and_keeps_the_reason():
    out = derive_from_reports([
        (D(2026, 10, 20), True, [_task("A107", "delayed", 0, "The concrete will be poured tomorrow morning. ")]),
    ])
    a = out["A107"]
    assert a["status"] == "not_started"
    assert a["actual_start"] is None
    assert a["reason"] == "The concrete will be poured tomorrow morning."


def test_first_start_and_first_finish_win_in_date_order_whatever_the_input_order():
    out = derive_from_reports([
        (D(2026, 10, 23), False, [_task("A107", "completed", 0)]),
        (D(2026, 10, 21), False, [_task("A107", "in_progress", 40)]),
        (D(2026, 10, 20), True, [_task("A107", "delayed", 0, "tomorrow")]),
    ])
    a = out["A107"]
    assert (a["actual_start"], a["actual_finish"]) == (D(2026, 10, 21), D(2026, 10, 23))
    assert a["status"] == "done" and a["percent_complete"] == 100
    assert a["reason"] is None          # it started; the old delay note is history
    assert a["from_draft"] is False     # the dates came from submitted reports


def test_draft_reports_count_but_are_flagged():
    a = derive_from_reports([(D(2026, 10, 16), True, [_task("A60", "in_progress", 50)])])["A60"]
    assert a["actual_start"] == D(2026, 10, 16) and a["from_draft"] is True


def test_removed_tasks_are_ignored():
    assert derive_from_reports([(D(2026, 10, 16), False, [_task("A60", "completed", 100, removed=True)])]) == {}


# ── API: merge with typed-in facts ────────────────────────────────────────

def _office(db, role="manager"):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, :r)"),
               {"i": str(uid), "e": f"{uid}@t.local", "r": role})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)


def _project(db, member):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,'P',:s,'lead')"),
               {"i": str(pid), "s": f"p-{str(pid)[:8]}"})
    db.execute(_T("insert into project_members (project_id, user_id, role) values (:p,:u,'member')"),
               {"p": str(pid), "u": member.id})
    return pid


def test_typed_fields_override_report_fields_one_by_one(db_session):
    u = _office(db_session)
    pid = _project(db_session, u)
    db_session.add(DailySiteReport(project_id=pid, report_date=D(2026, 10, 21), status="submitted",
                                   payload={"tasks": [_task("A107", "in_progress", 30)]}))
    db_session.flush()

    # finished on a day nobody filed a report
    out = record_actual(pid, "A107", ActualIn(actual_finish=D(2026, 10, 24), status="done"),
                        db=db_session, user=u)
    assert out["actual_start"] == D(2026, 10, 21)    # still from the report
    assert out["actual_finish"] == D(2026, 10, 24)   # typed
    assert out["status"] == "done" and out["source"] == "both"

    codes = [r["activity_code"] for r in list_actuals(pid, db=db_session, user=u)["actuals"]]
    assert codes == ["A107"]


def test_finish_before_start_is_refused(db_session):
    u = _office(db_session)
    pid = _project(db_session, u)
    with pytest.raises(HTTPException) as e:
        record_actual(pid, "A10", ActualIn(actual_start=D(2026, 10, 24), actual_finish=D(2026, 10, 20)),
                      db=db_session, user=u)
    assert e.value.status_code == 422


def test_a_non_member_cannot_read_another_projects_actuals(db_session):
    owner = _office(db_session)
    pid = _project(db_session, owner)
    stranger = _office(db_session)
    with pytest.raises(HTTPException) as e:
        list_actuals(pid, db=db_session, user=stranger)
    assert e.value.status_code in (403, 404)


def test_finish_before_a_start_from_a_site_report_is_refused(db_session):
    # The first check compared only the dates typed in the same request, so a
    # finish on 18 Oct was accepted against a 21 Oct start from a site report.
    u = _office(db_session)
    pid = _project(db_session, u)
    db_session.add(DailySiteReport(project_id=pid, report_date=D(2026, 10, 21), status="submitted",
                                   payload={"tasks": [_task("A107", "in_progress", 30)]}))
    db_session.flush()
    with pytest.raises(HTTPException) as e:
        record_actual(pid, "A107", ActualIn(status="done", actual_finish=D(2026, 10, 18)),
                      db=db_session, user=u)
    assert e.value.status_code == 422
    assert "before actual start 2026-10-21" in e.value.detail


def test_a_date_in_the_future_is_refused(db_session):
    from datetime import timedelta
    from app.services.actuals import site_today
    u = _office(db_session)
    pid = _project(db_session, u)
    with pytest.raises(HTTPException) as e:
        record_actual(pid, "A110", ActualIn(status="started", actual_start=site_today() + timedelta(days=1)),
                      db=db_session, user=u)
    assert e.value.status_code == 422 and "future" in e.value.detail
    # today is fine
    out = record_actual(pid, "A110", ActualIn(status="started", actual_start=site_today()),
                        db=db_session, user=u)
    assert out["actual_start"] == site_today()
