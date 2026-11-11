"""Client › Proposal: the office sends a presentation to the client portal;
the client accepts it there by typing their name (round 2, R2Proposal).

Acceptance is a record (who, when, IP, browser, the price shown). It never
creates or changes a contract.
"""
from __future__ import annotations

import inspect
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser, require_office  # noqa: E402
from app.routers import portal  # noqa: E402
from app.routers import proposal_portal as office  # noqa: E402
from app.services import proposal_portal as svc  # noqa: E402

TODAY = date(2026, 11, 2)
BUDGET = {"subtotal": 188048.29, "gst": 9402.41, "total": 197450.70, "validDays": 30}


@pytest.fixture(autouse=True)
def _tables(db_session, monkeypatch):
    db = db_session
    db.execute(_T("""CREATE TABLE IF NOT EXISTS presentations (
        id uuid PRIMARY KEY, project_id uuid, audience text, version_number int, label text, status text,
        payload jsonb, share_token text, created_at timestamptz DEFAULT now(), updated_at timestamptz)"""))
    for col in ("portal_sent_at timestamptz", "portal_sent_by_id uuid", "portal_withdrawn_at timestamptz",
                "portal_opened_at timestamptz", "portal_open_count integer NOT NULL DEFAULT 0",
                "based_on_estimate_id uuid"):
        db.execute(_T(f"ALTER TABLE presentations ADD COLUMN IF NOT EXISTS {col}"))
    db.execute(_T("""CREATE TABLE IF NOT EXISTS proposal_acceptances (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), presentation_id uuid NOT NULL UNIQUE, project_id uuid NOT NULL,
        user_id uuid, typed_name text NOT NULL, version_number int, subtotal numeric(12,2), gst numeric(12,2),
        total numeric(12,2), valid_until date, ip text, user_agent text,
        accepted_at timestamptz NOT NULL DEFAULT now())"""))
    db.execute(_T("ALTER TABLE proposal_acceptances ADD COLUMN IF NOT EXISTS options jsonb NOT NULL DEFAULT '[]'::jsonb"))
    for col in ("project_id uuid", "version_number int", "label text", "payload jsonb", "is_active boolean"):
        db.execute(_T(f"ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS {col}"))
    db.execute(_T("""CREATE TABLE IF NOT EXISTS audit_logs (
        id bigserial PRIMARY KEY, project_id uuid, table_name text, row_id uuid, action text,
        user_id uuid, diff jsonb, created_at timestamptz DEFAULT now())"""))
    db.execute(_T("""CREATE TABLE IF NOT EXISTS contracts (
        id uuid PRIMARY KEY, project_id uuid, version_number int, base_contract_value numeric,
        status text, payload jsonb)"""))
    monkeypatch.setattr(svc, "site_today", lambda: TODAY)
    monkeypatch.setattr("app.services.notify._send", lambda *a, **k: None)


def _user(db, role):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, :r)"),
               {"i": str(uid), "e": f"{uid}@t.local", "r": role})
    return CurrentUser(id=str(uid), email=f"{uid}@t.local", role=role)


def _setup(db):
    me, client = _user(db, "manager"), _user(db, "owner")
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) values (:i,'Kitchen',:s,'lead')"),
               {"i": str(pid), "s": f"p-{str(pid)[:8]}"})
    for u in (me, client):
        db.execute(_T("insert into project_members (project_id, user_id, role) values (:p,:u,'member')"),
                   {"p": str(pid), "u": u.id})
    prs = []
    for v in (1, 2):
        prid = uuid4()
        db.execute(_T("""insert into presentations (id, project_id, audience, version_number, label, status, payload)
                         values (:i, :p, 'client', :v, :l, 'draft', CAST(:j AS jsonb))"""),
                   {"i": prid, "p": str(pid), "v": v, "l": f"Proposal v{v}",
                    "j": json.dumps({"headline": f"Kitchen v{v}", "budget": BUDGET, "provenance": "INTERNAL"})})
        prs.append(prid)
    return me, client, pid, prs


def _req(ip="203.0.113.7"):
    return SimpleNamespace(headers={"x-forwarded-for": f"{ip}, 10.0.0.1", "user-agent": "Safari"},
                           client=SimpleNamespace(host="10.0.0.1"))


def test_nothing_is_in_the_portal_until_the_office_sends_it(db_session):
    me, client, pid, (v1, v2) = _setup(db_session)
    assert portal.portal_proposal(pid, db_session, client) == {"proposal": None}
    office.send_to_portal(pid, v1, db_session, me)
    got = portal.portal_proposal(pid, db_session, client)["proposal"]
    assert got["version_number"] == 1 and got["total"] == 197450.70
    assert got["valid_until"] is not None and "provenance" not in got  # internal fields never go out
    # the client is told in the thread
    assert db_session.execute(_T("""select count(*) from project_messages where project_id = :p
                                    and sender_role = 'office'"""), {"p": str(pid)}).scalar() == 1
    # sending v2 replaces v1 in the portal
    office.send_to_portal(pid, v2, db_session, me)
    assert portal.portal_proposal(pid, db_session, client)["proposal"]["version_number"] == 2
    office.withdraw_from_portal(pid, v2, db_session, me)
    assert portal.portal_proposal(pid, db_session, client) == {"proposal": None}


def test_the_client_accepts_by_typing_their_name_and_nothing_else_changes(db_session):
    me, client, pid, (v1, _v2) = _setup(db_session)
    office.send_to_portal(pid, v1, db_session, me)
    with pytest.raises(HTTPException) as e:
        portal.portal_proposal_accept(pid, v1, portal.ProposalAccept(typed_name="Ben Lee"), _req(), db_session, client)
    assert e.value.status_code == 422  # box not ticked
    with pytest.raises(HTTPException):
        portal.portal_proposal_accept(pid, v1, portal.ProposalAccept(typed_name=" ", agree=True), _req(),
                                      db_session, client)
    out = portal.portal_proposal_accept(pid, v1, portal.ProposalAccept(typed_name="Ben  Lee", agree=True), _req(),
                                        db_session, client)["proposal"]
    assert out["accepted"]["typed_name"] == "Ben Lee" and out["accepted"]["total"] == 197450.70
    row = db_session.execute(_T("select ip, user_agent, version_number from proposal_acceptances")).one()
    assert row == ("203.0.113.7", "Safari", 1)
    assert db_session.execute(_T("select count(*) from contracts where project_id = :p"),
                              {"p": str(pid)}).scalar() == 0
    status_ = {s["version_number"]: s for s in office.proposal_status(pid, db_session, me)}
    assert status_[1]["accepted"]["typed_name"] == "Ben Lee"
    # can't accept twice, withdraw, or send another after acceptance
    with pytest.raises(HTTPException):
        portal.portal_proposal_accept(pid, v1, portal.ProposalAccept(typed_name="Ben Lee", agree=True), _req(),
                                      db_session, client)
    with pytest.raises(HTTPException):
        office.withdraw_from_portal(pid, v1, db_session, me)
    with pytest.raises(HTTPException):
        office.send_to_portal(pid, _v2, db_session, me)


def test_an_expired_price_cant_be_accepted(db_session):
    me, client, pid, (v1, _) = _setup(db_session)
    office.send_to_portal(pid, v1, db_session, me)
    db_session.execute(_T("update presentations set portal_sent_at = :t where id = :i"),
                       {"t": datetime(2026, 9, 20, 18, tzinfo=timezone.utc), "i": v1})
    assert portal.portal_proposal(pid, db_session, client)["proposal"]["expired"] is True
    with pytest.raises(HTTPException) as e:
        portal.portal_proposal_accept(pid, v1, portal.ProposalAccept(typed_name="Ben Lee", agree=True), _req(),
                                      db_session, client)
    assert "valid until Oct 20, 2026" in e.value.detail


def test_only_the_client_accepts_and_only_the_office_sends(db_session):
    me, client, pid, (v1, _) = _setup(db_session)
    office.send_to_portal(pid, v1, db_session, me)
    boss = _user(db_session, "superadmin")  # can preview the portal, can't accept for the client
    assert portal.portal_proposal(pid, db_session, boss)["proposal"]["version_number"] == 1
    with pytest.raises(HTTPException) as e:
        portal.portal_proposal_accept(pid, v1, portal.ProposalAccept(typed_name="Amir", agree=True), _req(),
                                      db_session, boss)
    assert e.value.status_code == 403
    stranger = _user(db_session, "owner")
    with pytest.raises(HTTPException) as e:
        portal.portal_proposal(pid, db_session, stranger)
    assert e.value.status_code in (403, 404)
    for route in office.router.routes:
        deps = [p.default.dependency for p in inspect.signature(route.endpoint).parameters.values()
                if hasattr(p.default, "dependency")]
        assert require_office in deps, route.path



OPT_BUDGET = {"subtotal": 110000.0, "gst": 5500.0, "total": 115500.0, "validDays": 30, "allowances": 0,
              "categories": [{"name": "Kitchen", "displayTotal": 100000.0, "optional": False},
                             {"name": "Heated floor", "displayTotal": 10000.0, "optional": True}]}


def test_the_client_can_drop_an_option_and_the_price_follows(db_session):
    me, client, pid, (v1, _) = _setup(db_session)
    db_session.execute(_T("update presentations set payload = jsonb_set(payload, '{budget}', CAST(:b AS jsonb)) where id = :i"),
                       {"b": json.dumps(OPT_BUDGET), "i": v1})
    office.send_to_portal(pid, v1, db_session, me)
    got = portal.portal_proposal(pid, db_session, client)["proposal"]
    assert got["base"] == 100000.0 and got["options"] == [{"name": "Heated floor", "amount": 10000.0}]
    with pytest.raises(HTTPException):
        portal.portal_proposal_accept(pid, v1, portal.ProposalAccept(typed_name="Ben Lee", agree=True, options=["Pool"]),
                                      _req(), db_session, client)
    out = portal.portal_proposal_accept(pid, v1, portal.ProposalAccept(typed_name="Ben Lee", agree=True, options=[]),
                                        _req(), db_session, client)["proposal"]
    assert out["accepted"]["total"] == 105000.0 and out["accepted"]["options"] == []


def test_opening_is_counted_for_the_client_not_the_office_preview(db_session):
    me, client, pid, (v1, _) = _setup(db_session)
    office.send_to_portal(pid, v1, db_session, me)
    boss = _user(db_session, "superadmin")
    portal.portal_proposal(pid, db_session, boss)
    assert office.proposal_status(pid, db_session, me)[0]["open_count"] == 0
    portal.portal_proposal(pid, db_session, client)
    portal.portal_proposal(pid, db_session, client)
    st = office.proposal_status(pid, db_session, me)[0]
    assert st["open_count"] == 2 and st["opened_at"]


def test_words_change_only_while_the_client_cant_see_them(db_session):
    me, client, pid, (v1, _) = _setup(db_session)
    office.edit_proposal_text(pid, v1, office.TextIn(headline="Kitchen, done right"), db_session, me)
    assert office.proposal_status(pid, db_session, me)[0]["headline"] == "Kitchen, done right"
    office.send_to_portal(pid, v1, db_session, me)
    with pytest.raises(HTTPException):
        office.edit_proposal_text(pid, v1, office.TextIn(headline="Changed"), db_session, me)
    portal.portal_proposal_accept(pid, v1, portal.ProposalAccept(typed_name="Ben Lee", agree=True), _req(), db_session, client)
    with pytest.raises(HTTPException):
        office.edit_proposal_text(pid, v1, office.TextIn(headline="Changed"), db_session, me)


def test_a_proposal_from_an_older_estimate_says_so(db_session):
    me, _, pid, (v1, _) = _setup(db_session)
    old, new = uuid4(), uuid4()
    db_session.execute(_T("update presentations set based_on_estimate_id = :e where id = :i"), {"e": old, "i": v1})
    db_session.execute(_T("""insert into estimate_versions (id, project_id, version_number, label, payload, is_active)
                             values (:i, :p, 5, 'v5', CAST(:j AS jsonb), true)"""),
                       {"i": new, "p": str(pid), "j": json.dumps({"categories": [{"name": "A", "total": 100000}]})})
    st = office.proposal_status(pid, db_session, me)[0]
    assert st["stale"] == {"estimate_version": 5, "estimate_total": 105000.0}
