"""Client › Conversation: portal messages and logged emails/calls as one
timeline, grouped by who they're with (round 2, approved 2026-10-23).

The client still sees only portal messages: the timeline reads both tables and
copies nothing.
"""
from __future__ import annotations

import inspect
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser, require_office  # noqa: E402
from app.routers import conversation as router  # noqa: E402

T0 = datetime(2026, 10, 12, 16, tzinfo=timezone.utc)


def _user(db, role="manager"):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, :r)"),
               {"i": str(uid), "e": f"{uid}@t.local", "r": role})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)


def _project(db, member):
    cid, pid = uuid4(), uuid4()
    db.execute(_T("""insert into clients (id, name, normalized_name, email, emails, kind)
                     values (:i, 'Ben Lee', 'ben lee', 'ben@lee.example', ARRAY['ben.k@work.example'], 'client')"""),
               {"i": cid})
    db.execute(_T("insert into projects (id, name, slug, status, client_id) values (:i,'1234',:s,'in_progress',:c)"),
               {"i": str(pid), "s": f"p-{str(pid)[:8]}", "c": cid})
    db.execute(_T("insert into project_members (project_id, user_id, role) values (:p,:u,'member')"),
               {"p": str(pid), "u": member.id})
    db.execute(_T("""insert into project_team (id, project_id, role, name, company, email)
                     values (:i, :p, 'geotechnical_engineer', 'Raj', 'GEO Engineers', 'raj@geo.example')"""),
               {"i": uuid4(), "p": str(pid)})
    db.execute(_T("""insert into suppliers (id, name, email, active)
                     values (:i, 'Sample Form Rentals', 'rentals@forms.example', true)"""), {"i": uuid4()})
    return pid


def _msg(db, pid, role, at, body="hi"):
    db.execute(_T("""insert into project_messages (id, project_id, sender_role, body, created_at)
                     values (:i, :p, :r, :b, :t)"""), {"i": uuid4(), "p": str(pid), "r": role, "b": body, "t": at})


def _comm(db, pid, at, **kw):
    row = {"source": "manual", "direction": "inbound", "channel": "email", "from_name": None, "from_email": None,
           "to_emails": None, "subject": None, "body": "text", "category": None, "action_items": "[]"}
    row.update(kw)
    db.execute(_T("""insert into project_communications (id, project_id, source, direction, channel, from_name,
                     from_email, to_emails, subject, body, occurred_at, category, action_items)
                     values (:i, :p, :source, :direction, :channel, :from_name, :from_email, :to_emails, :subject,
                     :body, :t, :category, CAST(:action_items AS jsonb))"""),
               {"i": uuid4(), "p": str(pid), "t": at, **row})


def test_one_timeline_grouped_by_who_it_is_with(db_session):
    me = _user(db_session)
    pid = _project(db_session, me)
    _msg(db_session, pid, "owner", T0, "When does framing start?")
    _comm(db_session, pid, T0 + timedelta(hours=1), from_email="Ben Lee <ben.k@work.example>", body="forwarded")
    _comm(db_session, pid, T0 + timedelta(hours=2), from_email="raj@geo.example", body="[KS-M8] booked")
    _comm(db_session, pid, T0 + timedelta(hours=3), from_name="Sample Form Rentals", body="delivery Wed")
    _comm(db_session, pid, T0 + timedelta(hours=4), channel="call", direction="outbound",
          to_emails="someone@else.example", body="called the city")
    out = router.get_conversation(pid, db_session, me)
    assert [i["group"] for i in out["items"]] == ["other", "suppliers", "engineers", "client", "client"]
    assert out["items"][-1]["kind"] == "portal" and out["items"][-1]["author"] == "Ben Lee"
    assert out["counts"] == {"client": 2, "engineers": 1, "suppliers": 1, "other": 1}
    assert out["client"]["emails"] == ["ben@lee.example", "ben.k@work.example"]


def test_needs_a_reply_until_we_answer(db_session):
    me = _user(db_session)
    pid = _project(db_session, me)
    _msg(db_session, pid, "office", T0, "Framing starts 30 Oct")
    _msg(db_session, pid, "owner", T0 + timedelta(hours=1), "Can we add a window?")
    _comm(db_session, pid, T0, from_email="raj@geo.example", action_items='["Send the shop drawing"]')
    out = router.get_conversation(pid, db_session, me)
    assert out["needs_reply"] == 2
    assert {q["item"] for q in out["open_questions"]} == {"Can we add a window?", "Send the shop drawing"}
    _msg(db_session, pid, "office", T0 + timedelta(hours=2), "Yes, I'll price it")
    _comm(db_session, pid, T0 + timedelta(hours=3), direction="outbound", to_emails="raj@geo.example", body="attached")
    assert router.get_conversation(pid, db_session, me)["needs_reply"] == 0


def test_opening_it_marks_the_clients_portal_messages_read(db_session):
    me = _user(db_session)
    pid = _project(db_session, me)
    _msg(db_session, pid, "owner", T0)
    out = router.get_conversation(pid, db_session, me)
    assert out["items"][0]["read"] is False  # as it was when opened
    unread = db_session.execute(_T("""select count(*) from project_messages
                                      where project_id = :p and read_at is null"""), {"p": str(pid)}).scalar()
    assert unread == 0


def test_the_conversation_is_office_only_and_checks_the_project(db_session):
    for route in router.router.routes:
        deps = [p.default.dependency for p in inspect.signature(route.endpoint).parameters.values()
                if hasattr(p.default, "dependency")]
        assert require_office in deps, route.path
    with pytest.raises(HTTPException) as e:
        require_office(CurrentUser(id=str(uuid4()), email="crew@t.local", role="worker"))
    assert e.value.status_code == 403
    outsider = _user(db_session)
    pid = _project(db_session, _user(db_session))
    with pytest.raises(HTTPException) as e:
        router.get_conversation(pid, db_session, outsider)
    assert e.value.status_code in (403, 404)


def test_decisions_on_record_bookings_and_a_schedule_reply(db_session):
    """R2Client: decisions from the records that hold them; a reply filed
    against a waiting booking offers to mark it booked; a reply draft from the
    schedule the CLIENT sees (not a newer internal version)."""
    from app.services.actuals import site_today  # noqa: F401
    import app.services.actuals as actuals_mod
    me = _user(db_session)
    pid = _project(db_session, me)
    db_session.execute(_T("""insert into change_orders (id, project_id, number, title, status, cost_impact, decided_at)
                             values (:i, :p, 3, 'Cladding upgrade', 'approved', 10200, '2026-08-31')"""),
                       {"i": uuid4(), "p": str(pid)})
    db_session.execute(_T("""insert into client_selections (id, project_id, title, decision_type, options, status,
                             selected_option_id, decided_at) values (:i, :p, 'Garage door', 'select',
                             CAST(:o AS jsonb), 'submitted', 'opt-2', now())"""),
                       {"i": uuid4(), "p": str(pid), "o": '[{"id":"opt-1","label":"As quoted"},{"id":"opt-2","label":"Upgrade"}]'})
    cid = uuid4()
    db_session.execute(_T("""insert into project_communications (id, project_id, source, direction, channel, from_email,
                             body, occurred_at, action_items) values (:i, :p, 'forward', 'inbound', 'email',
                             'raj@geo.example', '[KS-M8] Tuesday works', :t, '[]'::jsonb)"""),
                       {"i": cid, "p": str(pid), "t": T0})
    mid = uuid4()
    db_session.execute(_T("""insert into project_milestones (id, project_id, ref, stage, title, party_role, status, thread)
                             values (:i, :p, 'M8', 'foundation', 'GEO footings', 'geotechnical_engineer', 'requested',
                             CAST(:th AS jsonb))"""),
                       {"i": mid, "p": str(pid), "th": json.dumps([{"direction": "in", "communication_id": str(cid)}])})
    out = router.get_conversation(pid, db_session, me)
    what = [d["what"] for d in out["decisions"]]
    assert "CO-3 Cladding upgrade approved" in what and "Garage door: Upgrade" in what
    booked = [i for i in out["items"] if i.get("booking")]
    assert booked and booked[0]["booking"]["ref"] == "M8"
    # schedule reply: v1 is client-visible, v2 (internal) is not
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS schedules (id uuid PRIMARY KEY, project_id uuid,
        version_number int, p50_finish_date date, client_visible boolean NOT NULL DEFAULT true)"""))
    db_session.execute(_T("""CREATE TABLE IF NOT EXISTS activities (id uuid PRIMARY KEY, schedule_id uuid,
        activity_code text, name text, phase text, start_date date, finish_date date, is_milestone boolean)"""))
    v1, v2 = uuid4(), uuid4()
    db_session.execute(_T("""insert into schedules (id, project_id, version_number, p50_finish_date, client_visible)
                             values (:a, :p, 1, '2027-01-15', true), (:b, :p, 2, '2027-01-29', false)"""),
                       {"a": v1, "b": v2, "p": str(pid)})
    db_session.execute(_T("""insert into activities (id, schedule_id, name, start_date, finish_date, is_milestone)
                             values (:i, :s, 'Wall Framing', '2026-10-30', '2026-11-06', false)"""),
                       {"i": uuid4(), "s": v1})
    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(actuals_mod, "site_today", lambda: __import__("datetime").date(2026, 11, 2))
    try:
        txt = router.schedule_reply(pid, db_session, me)["text"]
    finally:
        mp.undo()
    assert "Wall Framing" in txt and "Jan 15" in txt and "Jan 29" not in txt
