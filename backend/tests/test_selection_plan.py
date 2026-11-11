"""Selections planned against the schedule (round 2 board R2Selections).

Before: a selection went to the client the moment it was added, had only a
hand-typed "needed by", and confirming a choice over the allowance changed
nothing in Money. Now: drafts until sent; decide-by = the activity's start
(latest schedule, a recorded start wins) minus the lead time; confirming a
priced choice drafts a change order for the difference.
"""
from __future__ import annotations

import inspect
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser, require_office  # noqa: E402
from app.routers import portal  # noqa: E402
from app.routers import selections as router  # noqa: E402
from app.services import actuals as actuals_svc  # noqa: E402
from app.services import selection_plan as svc  # noqa: E402

V7 = json.loads((Path(__file__).parent / "fixtures" / "schedule_sample_v7.json").read_text())
ACT = {a["activity_code"]: a for a in V7["activities"]}
TODAY = date(2026, 10, 30)


@pytest.fixture(autouse=True)
def _tables(db_session, monkeypatch):
    db = db_session
    db.execute(_T("""CREATE TABLE IF NOT EXISTS schedules (
        id uuid PRIMARY KEY, project_id uuid, version_number int, project_start date, holidays jsonb,
        p50_finish_date date, client_visible boolean NOT NULL DEFAULT true,
        created_at timestamptz DEFAULT clock_timestamp())"""))
    db.execute(_T("""CREATE TABLE IF NOT EXISTS activities (
        id uuid PRIMARY KEY, schedule_id uuid, activity_code text, name text, phase text,
        start_date date, finish_date date, is_milestone boolean)"""))
    # create_all doesn't add 0087's columns to a table made before them.
    for col in ("activity_code text", "lead_time_days int", "allowance numeric(12,2)", "sent_at timestamptz",
                "change_order_id uuid"):
        db.execute(_T(f"ALTER TABLE client_selections ADD COLUMN IF NOT EXISTS {col}"))
    for mod in (actuals_svc, svc):
        monkeypatch.setattr(mod, "site_today", lambda: TODAY)
    monkeypatch.setattr("app.services.notify._send", lambda *a, **k: None)


def _user(db, role="manager"):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, :r)"),
               {"i": str(uid), "e": f"{uid}@t.local", "r": role})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)


def _setup(db):
    me, client = _user(db), _user(db, "owner")
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,'1234',:s,'in_progress')"),
               {"i": str(pid), "s": f"p-{str(pid)[:8]}"})
    for u in (me, client):
        db.execute(_T("insert into project_members (project_id, user_id, role) values (:p,:u,'member')"),
                   {"p": str(pid), "u": u.id})
    sid = uuid4()
    db.execute(_T("insert into schedules (id, project_id, version_number, project_start) values (:i,:p,7,'2026-10-05')"),
               {"i": sid, "p": str(pid)})
    for a in V7["activities"]:
        db.execute(_T("""insert into activities (id, schedule_id, activity_code, name, phase, start_date, finish_date,
                         is_milestone) values (:i,:s,:c,:n,:ph,:sd,:fd,:m)"""),
                   {"i": uuid4(), "s": sid, "c": a["activity_code"], "n": a["name"], "ph": a["phase"],
                    "sd": a["start_date"], "fd": a["finish_date"], "m": a["is_milestone"]})
    return me, client, pid


OPTS = [{"label": "As quoted", "price_delta": 0}, {"label": "Upgrade with windows row", "price_delta": 1850}]


def _add(db, pid, me, title="Garage door", code="A180", lead=28, opts=OPTS):
    return router.create_selection(pid, router.SelectionCreate(title=title, options=opts, activity_code=code,
                                                               lead_time_days=lead, allowance=4200), db, me)


def test_a_new_selection_is_a_draft_until_sent(db_session):
    me, client, pid = _setup(db_session)
    s = _add(db_session, pid, me)
    assert portal.portal_selections(pid, db_session, client) == []
    with pytest.raises(HTTPException):  # the client can't answer what they weren't sent
        portal.portal_selection_decide(pid, s["id"], portal.SelectionDecision(option_id="opt-1"), db_session, client)
    router.send_selection(pid, s["id"], db_session, me)
    shown = portal.portal_selections(pid, db_session, client)
    assert [x["title"] for x in shown] == ["Garage door"]
    assert "lead_time_days" not in shown[0] and shown[0]["decide_by"]


def test_decide_by_is_the_activity_start_minus_the_lead_time(db_session):
    me, client, pid = _setup(db_session)
    _add(db_session, pid, me)
    _add(db_session, pid, me, title="Tile", code="A310", lead=21)
    p = router.selection_plan(pid, db_session, me)
    start = date.fromisoformat(ACT["A180"]["start_date"])
    by = {r["title"]: r for r in p["selections"]}
    assert by["Garage door"]["decide_by"] == str(start - timedelta(days=28))
    assert [r["title"] for r in p["selections"]] == ["Garage door", "Tile"]  # in the order the work needs them
    assert by["Garage door"]["state"] == "not_sent"
    # the garage door work started early on site → decide-by moves with it, and is now late
    actuals_svc.record(db_session, pid, "A180", {"status": "started", "actual_start": date(2026, 10, 20)}, None)
    by = {r["title"]: r for r in router.selection_plan(pid, db_session, me)["selections"]}
    assert by["Garage door"]["decide_by"] == "2026-09-22" and by["Garage door"]["late"] is True


def test_suggestions_come_from_the_schedules_own_work(db_session):
    me, _, pid = _setup(db_session)
    _add(db_session, pid, me)
    sug = {s["title"]: s for s in router.selection_suggestions(pid, db_session, me)}
    assert "Garage door" not in sug  # already listed
    assert sug["Windows & exterior doors"]["activity_code"] == "A170"
    assert sug["Tile"]["activity_code"] == "A310" and sug["Tile"]["lead_time_days"] == 21
    assert "Appliances" not in sug  # 1234 has no appliance work


def test_confirming_a_choice_over_the_allowance_drafts_a_change_order(db_session):
    me, client, pid = _setup(db_session)
    s = _add(db_session, pid, me)
    router.send_selection(pid, s["id"], db_session, me)
    with pytest.raises(HTTPException):  # nothing to confirm before the client answers
        router.confirm_selection(pid, s["id"], db_session, me)
    portal.portal_selection_decide(pid, s["id"], portal.SelectionDecision(option_id="opt-2"), db_session, client)
    with pytest.raises(HTTPException):  # confirming goes through /confirm, never a bare status change
        router.update_selection(pid, s["id"], router.SelectionUpdate(status="confirmed"), db_session, me)
    out = router.confirm_selection(pid, s["id"], db_session, me)
    assert out["status"] == "confirmed" and out["change_order"]["cost_impact"] == 1850.0
    co = db_session.execute(_T("select number, status, title from change_orders where project_id = :p"),
                            {"p": str(pid)}).one()
    assert co.number == 1 and co.status == "draft" and "Garage door" in co.title


def test_a_choice_at_the_allowance_confirms_without_a_change_order(db_session):
    me, client, pid = _setup(db_session)
    s = _add(db_session, pid, me)
    router.send_selection(pid, s["id"], db_session, me)
    portal.portal_selection_decide(pid, s["id"], portal.SelectionDecision(option_id="opt-1"), db_session, client)
    assert router.confirm_selection(pid, s["id"], db_session, me)["change_order"] is None
    assert db_session.execute(_T("select count(*) from change_orders where project_id = :p"),
                              {"p": str(pid)}).scalar() == 0


def test_a_select_with_no_options_cant_be_sent(db_session):
    me, _, pid = _setup(db_session)
    s = _add(db_session, pid, me, opts=[])
    with pytest.raises(HTTPException) as e:
        router.send_selection(pid, s["id"], db_session, me)
    assert e.value.status_code == 422


def test_the_new_selection_routes_are_office_only():
    for route in router.router.routes:
        if route.path.endswith(("/plan", "/suggestions", "/send", "/confirm")):
            deps = [p.default.dependency for p in inspect.signature(route.endpoint).parameters.values()
                    if hasattr(p.default, "dependency")]
            assert require_office in deps, route.path
