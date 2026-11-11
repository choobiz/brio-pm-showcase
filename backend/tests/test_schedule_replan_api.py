"""Update the plan: preview writes nothing, save makes v8 from v7, and the
client keeps seeing v7 until the office ticks "Tell the client".

Before this, every portal and client-package reader took the LATEST schedule,
so any saved version reached the client at once (found 2026-10-30 while
building S4; the approved sketch promised the opposite).
"""
from __future__ import annotations

import json
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
from app.routers import portal  # noqa: E402
from app.routers.schedule_replan import ReplanIn, preview_replan, save_replan  # noqa: E402
from app.services import actuals as actuals_svc  # noqa: E402
from app.services.package_pdf import load_schedule  # noqa: E402

V7 = json.loads((Path(__file__).parent / "fixtures" / "schedule_sample_v7.json").read_text())


@pytest.fixture(autouse=True)
def _tables(db_session, monkeypatch):
    """schedules / activities / inspections / audit_logs are migration-only."""
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS schedules (
        id uuid PRIMARY KEY, project_id uuid, version_number int, project_start date, working_hours text,
        holidays jsonb, deterministic_finish_date date, p50_finish_date date, p80_finish_date date,
        project_expected_wd numeric, project_std_dev_wd numeric, critical_path_activity_codes jsonb,
        notes text, parent_version_id uuid, created_by_id uuid, is_baseline boolean DEFAULT false,
        client_visible boolean NOT NULL DEFAULT true, created_at timestamptz DEFAULT clock_timestamp())"""))
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS activities (
        id uuid PRIMARY KEY, schedule_id uuid, activity_code text, name text, phase text,
        predecessor_codes jsonb, lag_wd int, optimistic_wd numeric, most_likely_wd numeric,
        pessimistic_wd numeric, expected_wd numeric, variance numeric, early_start_wd numeric,
        early_finish_wd numeric, late_start_wd numeric, late_finish_wd numeric, total_float_wd numeric,
        free_float_wd numeric, start_date date, finish_date date, is_critical boolean, is_milestone boolean)"""))
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS inspections (
        id uuid PRIMARY KEY, schedule_id uuid, activity_id uuid, authority text, inspection_type text,
        lead_time_days int, gates text, notes text)"""))
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS audit_logs (
        id bigserial PRIMARY KEY, project_id uuid, table_name text, row_id uuid, action text,
        user_id uuid, diff jsonb, created_at timestamptz DEFAULT now())"""))
    monkeypatch.setattr(actuals_svc, "site_today", lambda: date(2026, 10, 30))


def _office(db):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, 'manager')"),
               {"i": str(uid), "e": f"{uid}@t.local"})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role="manager")


def _project_with_v7(db, member):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,'1234',:s,'lead')"),
               {"i": str(pid), "s": f"p-{str(pid)[:8]}"})
    db.execute(_T("insert into project_members (project_id, user_id, role) values (:p,:u,'member')"),
               {"p": str(pid), "u": member.id})
    sid = uuid4()
    db.execute(_T("""insert into schedules (id, project_id, version_number, project_start, holidays,
                     p50_finish_date, p80_finish_date, project_std_dev_wd)
                     values (:i, :p, 7, :ps, CAST(:h AS jsonb), :p50, :p80, 4.5)"""),
               {"i": sid, "p": str(pid), "ps": V7["project_start"], "h": json.dumps(V7["holidays"]),
                "p50": V7["p50_finish_date"], "p80": V7["p80_finish_date"]})
    for a in V7["activities"]:
        aid = uuid4()
        db.execute(_T("""insert into activities (id, schedule_id, activity_code, name, phase, predecessor_codes,
                         lag_wd, most_likely_wd, expected_wd, early_start_wd, early_finish_wd, total_float_wd,
                         start_date, finish_date, is_critical, is_milestone)
                         values (:id, :s, :c, :n, :ph, CAST(:pr AS jsonb), :lag, :m, :e, :es, :ef, :tf,
                         :sd, :fd, :cr, :ms)"""),
                   {"id": aid, "s": sid, "c": a["activity_code"], "n": a["name"], "ph": a["phase"],
                    "pr": json.dumps(a["predecessor_codes"]), "lag": a["lag_wd"], "m": a["most_likely_wd"],
                    "e": a["expected_wd"], "es": a["early_start_wd"], "ef": a["early_finish_wd"],
                    "tf": a["total_float_wd"], "sd": a["start_date"], "fd": a["finish_date"],
                    "cr": a["is_critical"], "ms": a["is_milestone"]})
        if a["activity_code"] == "A105":
            db.execute(_T("""insert into inspections (id, schedule_id, activity_id, authority, inspection_type)
                             values (:i, :s, :a, 'GEO', 'formed footings')"""),
                       {"i": uuid4(), "s": sid, "a": aid})
    db.add(DailySiteReport(project_id=pid, report_date=date(2026, 10, 16), status="draft", payload={"tasks": [
        {"activity_code": "A100", "status": "in_progress", "percent_complete": 0},
        {"activity_code": "A60", "status": "not_started", "notes": "Shop drawing not cleared"}]}))
    db.add(DailySiteReport(project_id=pid, report_date=date(2026, 10, 21), status="submitted", payload={"tasks": [
        {"activity_code": "A107", "status": "delayed", "notes": "The concrete will be poured tomorrow morning."}]}))
    db.flush()
    return pid, sid


def _count(db, pid):
    return db.execute(_T("select count(*) from schedules where project_id = :p"), {"p": str(pid)}).scalar()


def test_preview_writes_nothing_and_shows_both_rules(db_session):
    u = _office(db_session)
    pid, sid = _project_with_v7(db_session, u)
    out = preview_replan(pid, unanswered="keep", db=db_session, user=u)
    assert out["base"]["version_number"] == 7 and out["data_date"] == "2026-10-30"
    assert out["p50_finish_date"] == "2027-01-19"
    assert str(out["client_p50_finish_date"]) == "2027-01-15"   # what the portal shows now
    assert out["other_rule"]["p50_finish_date"] == "2027-01-29"
    assert {c["activity_code"] for c in out["assumed"]} >= {"A75", "A72"}
    assert any(c["activity_code"] == "A100" for c in out["changes"])
    assert _count(db_session, pid) == 1


def test_save_makes_v8_on_v7_and_the_client_still_sees_v7(db_session):
    u = _office(db_session)
    pid, sid = _project_with_v7(db_session, u)
    out = save_replan(pid, ReplanIn(base_schedule_id=sid), db=db_session, user=u)
    assert out["version_number"] == 8 and out["client_visible"] is False
    row = db_session.execute(_T("select parent_version_id, notes, client_visible from schedules where id = :i"),
                             {"i": out["id"]}).mappings().one()
    assert str(row["parent_version_id"]) == str(sid) and row["client_visible"] is False
    assert "Built on v7" in row["notes"] and "CLIENT: NOT shown" in row["notes"]
    assert "A100 Form Footings: started" in row["notes"] and "draft" in row["notes"]
    # inspections ride along, re-linked to v8's own activity row
    assert db_session.execute(_T("""select a.activity_code from inspections i join activities a on a.id = i.activity_id
                                    where i.schedule_id = :i"""), {"i": out["id"]}).scalar() == "A105"
    # the portal and the client package still read v7
    assert str(portal._latest_schedule(db_session, pid)["id"]) == str(sid)
    assert load_schedule(db_session, str(pid))[0]["finishP50"] == "2027-01-15"


def test_tell_the_client_makes_the_new_version_visible(db_session):
    u = _office(db_session)
    pid, sid = _project_with_v7(db_session, u)
    out = save_replan(pid, ReplanIn(base_schedule_id=sid, client_visible=True), db=db_session, user=u)
    assert str(portal._latest_schedule(db_session, pid)["id"]) == out["id"]


def test_a_stale_preview_cannot_save_over_a_newer_version(db_session):
    u = _office(db_session)
    pid, sid = _project_with_v7(db_session, u)
    save_replan(pid, ReplanIn(base_schedule_id=sid), db=db_session, user=u)   # v8
    with pytest.raises(HTTPException) as e:
        save_replan(pid, ReplanIn(base_schedule_id=sid), db=db_session, user=u)   # still says v7
    assert e.value.status_code == 409 and "v8" in e.value.detail
    assert _count(db_session, pid) == 2


def test_a_non_member_cannot_preview(db_session):
    owner = _office(db_session)
    pid, _ = _project_with_v7(db_session, owner)
    with pytest.raises(HTTPException) as e:
        preview_replan(pid, unanswered="keep", db=db_session, user=_office(db_session))
    assert e.value.status_code in (403, 404)
