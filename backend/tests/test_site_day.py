"""Site tab (UI round 1, phase 6): the read-only day view, and the daily
report / hours routes refusing anyone not on the project."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.models.daily_site_report import DailySiteReport  # noqa: E402
from app.routers import daily_reports as D  # noqa: E402
from app.routers import time_tracking as T  # noqa: E402


@pytest.fixture(autouse=True)
def _schedule_tables(db_session):
    """`schedules` and `activities` are migration-only (raw SQL, no models), so
    the create_all test schema lacks them. Created inside the test transaction;
    the rollback removes them."""
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS schedules (
        id uuid PRIMARY KEY, project_id uuid, created_at timestamptz DEFAULT now())"""))
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS activities (
        schedule_id uuid, activity_code text, name text, phase text, start_date date,
        finish_date date, is_critical boolean, is_milestone boolean, predecessor_codes jsonb)"""))


def _project(db):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,'Site',:s,'lead')"),
               {"i": str(pid), "s": f"si-{str(pid)[:8]}"})
    return pid


def _user(db, role="superadmin"):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, :r)"),
               {"i": str(uid), "e": f"{uid}@t.local", "r": role})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)


def test_site_day_reads_without_creating_a_report(db_session):
    pid, su = _project(db_session), _user(db_session)
    out = D.site_day(project_id=pid, day=date(2026, 10, 7), db=db_session, user=su)
    assert out["report"] is None and len(out["week"]) == 7
    assert db_session.query(DailySiteReport).filter_by(project_id=pid).count() == 0


def test_week_strip_statuses(db_session):
    pid, su = _project(db_session), _user(db_session)
    on = date(2026, 10, 7)
    db_session.add_all([
        DailySiteReport(project_id=pid, report_date=on - timedelta(days=1), status="submitted", payload={}),
        DailySiteReport(project_id=pid, report_date=on, status="draft", payload={}),
    ])
    # Someone clocked in two days before, with no report: missing.
    db_session.execute(_T("insert into time_entries (id, user_id, project_id, punch_in_at, status) "
                          "values (:i, :u, :p, :t, 'closed')"),
                       {"i": str(uuid4()), "u": su.id, "p": str(pid),
                        "t": datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)})
    db_session.flush()
    week = {d["date"]: d["status"] for d in
            D.site_day(project_id=pid, day=on, db=db_session, user=su)["week"]}
    assert week["2026-10-06"] == "filed" and week["2026-10-07"] == "draft"
    assert week["2026-10-05"] == "missing" and week["2026-10-04"] == "quiet"


def test_today_is_pending_and_a_scheduled_weekend_is_not_missing(db_session, monkeypatch):
    pid, su = _project(db_session), _user(db_session)
    sid = uuid4()
    db_session.execute(_T("insert into schedules (id, project_id) values (:s, :p)"),
                       {"s": str(sid), "p": str(pid)})
    db_session.execute(_T("""insert into activities (schedule_id, activity_code, name, start_date,
        finish_date) values (:s, 'A1', 'Excavation', '2026-10-01', '2026-10-31')"""), {"s": str(sid)})
    monkeypatch.setattr(D, "_site_today", lambda: date(2026, 10, 13))  # a Tuesday
    week = {d["date"]: d["status"] for d in
            D.site_day(project_id=pid, day=None, db=db_session, user=su)["week"]}
    assert week["2026-10-13"] == "pending"
    assert week["2026-10-10"] == "quiet" and week["2026-10-11"] == "quiet"  # Sat, Sun
    assert week["2026-10-09"] == "missing"  # Fri


def test_site_day_lists_that_days_hours_with_names(db_session):
    pid, su = _project(db_session), _user(db_session)
    db_session.execute(_T("insert into time_entries (id, user_id, project_id, punch_in_at, status) "
                          "values (:i, :u, :p, :t, 'open')"),
                       {"i": str(uuid4()), "u": su.id, "p": str(pid),
                        "t": datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc)})
    out = D.site_day(project_id=pid, day=date(2026, 10, 7), db=db_session, user=su)
    assert len(out["hours"]) == 1 and out["offsite_count"] == 0


def test_daily_reports_and_hours_refuse_someone_not_on_the_project(db_session):
    pid = _project(db_session)
    outsider = _user(db_session, role="manager")
    for call in (
        lambda: D.list_daily_reports(project_id=pid, db=db_session, user=outsider),
        lambda: D.site_day(project_id=pid, day=None, db=db_session, user=outsider),
        lambda: T.list_time_entries(project_id=pid, db=db_session, user=outsider),
    ):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 403
    r = DailySiteReport(project_id=pid, report_date=date(2026, 10, 7), status="draft", payload={})
    db_session.add(r)
    db_session.flush()
    with pytest.raises(HTTPException) as e:
        D.get_daily_report(report_id=r.id, db=db_session, user=outsider)
    assert e.value.status_code == 403


def test_todays_scheduled_work_and_the_next_milestone(db_session):
    pid, su = _project(db_session), _user(db_session)
    sid = uuid4()
    db_session.execute(_T("insert into schedules (id, project_id) values (:s, :p)"),
                       {"s": str(sid), "p": str(pid)})
    db_session.execute(_T("""insert into activities (schedule_id, activity_code, name, start_date,
        finish_date, is_critical, is_milestone) values
        (:s, 'A10', 'Form footings', '2026-10-06', '2026-10-08', true, false),
        (:s, 'M1', 'Framing starts', '2026-11-04', '2026-11-04', false, true)"""), {"s": str(sid)})
    out = D.site_day(project_id=pid, day=date(2026, 10, 7), db=db_session, user=su)
    assert [a["name"] for a in out["scheduled"]] == ["Form footings"]
    assert out["next_milestone"] == {"label": "Framing starts", "date": "2026-11-04"}
    # Scheduled work and no report on an earlier day: missing.
    assert {d["date"]: d["status"] for d in out["week"]}["2026-10-06"] == "missing"
