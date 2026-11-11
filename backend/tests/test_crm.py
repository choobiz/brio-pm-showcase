"""CRM queue, contacts and interaction logging.

⚠️  These tests WRITE and DELETE. They refuse to run unless DATABASE_URL clearly
    points at a throwaway database. brio-pm lost its production data once to a
    test run; the guard below exists so that cannot happen again.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

TEST_DB_MARKERS = ("_test", "test_", "localhost:55432", "127.0.0.1:55432")


def _is_disposable(url: str) -> bool:
    return bool(url) and any(m in url for m in TEST_DB_MARKERS)


pytestmark = pytest.mark.skipif(
    not _is_disposable(os.environ.get("DATABASE_URL", "")),
    reason="DATABASE_URL does not look like a throwaway test database",
)


@pytest.fixture()
def env():
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    import app.main as M
    from app.auth import get_current_user
    from app.db import SessionLocal
    from app.models.client import Client
    from app.models.crm import CrmInteraction, CrmOpportunity
    from app.models.user import User

    db = SessionLocal()
    user = db.execute(select(User)).scalars().first()
    if user is None:
        user = User(email="test@example.com", name="Test User", role="admin")
        db.add(user)
        db.commit()

    db.query(CrmInteraction).delete()
    db.query(CrmOpportunity).delete()
    db.query(Client).delete()
    db.commit()

    seeds = [
        ("LQ Design Group Ltd.", "A", 228, "Call — 20+ permits/yr", -6),
        ("Architrix Design Studio Inc.", "A", 68, "Email re: recent projects", 0),
        ("Grey Consultants", "B", 27, None, None),          # no next action
        ("Dead Co Ltd.", "A", 40, "should never surface", -3),
    ]
    ids = {}
    for name, tier, permits, action, off in seeds:
        c = Client(
            name=name, normalized_name=name.lower(), kind="prospect",
            segment="design", tier=tier, permits_total=permits,
            contact_confidence="high", email=f"info@{name.split()[0].lower()}.com",
            excluded_reason="dissolved per BC registry" if "Dead" in name else None,
        )
        db.add(c)
        db.flush()
        ids[name] = c.id
        db.add(CrmOpportunity(
            client_id=c.id, track="construction", status="new",
            next_action=action,
            next_action_at=(date.today() + timedelta(days=off)) if off is not None else None,
            owner_id=user.id,
        ))
    db.commit()

    # the dependency override must return a SESSION-BOUND user
    M.app.dependency_overrides[get_current_user] = lambda: db.get(User, user.id)
    yield TestClient(M.app), db, user, ids
    M.app.dependency_overrides.clear()
    db.close()


def test_queue_classifies_and_orders(env):
    client, *_ = env
    d = client.get("/crm/queue").json()
    # counts also carries channel tallies; check the urgency keys specifically
    urgency = {k: d["counts"][k] for k in ("overdue", "due_today", "no_action", "upcoming")}
    assert urgency == {"overdue": 1, "due_today": 1, "no_action": 1, "upcoming": 0}
    # an opportunity with no next action is the most urgent thing in the list:
    # it is open and going nowhere, so it sorts first
    assert d["items"][0]["reason"] == "no_action"
    assert d["items"][1]["reason"] == "overdue"
    assert d["items"][1]["days_overdue"] == 6


def test_excluded_contacts_never_surface(env):
    """A dissolved company must not reach the queue, even with a due action."""
    client, *_ = env
    names = [i["contact"]["name"] for i in client.get("/crm/queue").json()["items"]]
    assert not any("Dead Co" in n for n in names)


def test_logging_advances_status_and_sets_next_action(env):
    client, _db, _u, ids = env
    d = client.get("/crm/queue").json()
    opp = next(i for i in d["items"] if i["contact"]["name"].startswith("LQ"))
    oid = opp["opportunity"]["id"]

    r = client.post(f"/crm/opportunities/{oid}/log", json={
        "channel": "call", "summary": "Spoke to Qi Li",
        "outcome": "Send capability one-pager",
        "next_action": "Send one-pager",
        "next_action_at": str(date.today() + timedelta(days=2)),
    })
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "working"          # first contact moves it out of 'new'
    assert j["next_action"] == "Send one-pager"

    detail = client.get(f"/crm/contacts/{opp['contact']['id']}").json()
    assert len(detail["interactions"]) == 1
    assert detail["interactions"][0]["outcome"] == "Send capability one-pager"

    # it now has a future action, so it drops out of today's queue
    assert client.get("/crm/queue").json()["counts"]["overdue"] == 0


def test_closing_removes_it_from_the_queue(env):
    client, *_ = env
    d = client.get("/crm/queue").json()
    oid = d["items"][1]["opportunity"]["id"]
    before = len(d["items"])
    r = client.post(f"/crm/opportunities/{oid}/log",
                    json={"channel": "email", "status": "won"})
    assert r.status_code == 200 and r.json()["status"] == "won"
    assert len(client.get("/crm/queue").json()["items"]) == before - 1


def test_one_opportunity_per_track(env):
    client, _db, _u, ids = env
    cid = str(ids["Grey Consultants"])
    dup = client.post(f"/crm/contacts/{cid}/opportunities",
                      json={"track": "construction"})
    assert dup.status_code == 409
    other = client.post(f"/crm/contacts/{cid}/opportunities", json={"track": "str"})
    assert other.status_code == 201


def test_cannot_promote_an_excluded_contact(env):
    client, _db, _u, ids = env
    r = client.post(f"/crm/contacts/{ids['Dead Co Ltd.']}/opportunities",
                    json={"track": "str"})
    assert r.status_code == 409
    assert "dissolved" in r.json()["detail"]


def test_channel_follows_the_contact_record_not_the_tier(env):
    """A firm with no phone must never be told to call it."""
    client, db, _u, ids = env
    from app.models.client import Client

    items = {i["contact"]["name"]: i for i in client.get("/crm/queue").json()["items"]}
    # seeds carry an email and no phone
    grey = items["Grey Consultants"]
    assert grey["channel"] == "email"
    assert grey["channel_target"] == "info@grey.example"

    # strip the email and it must fall through to research, not to a channel
    c = db.get(Client, ids["Grey Consultants"])
    c.email = None
    c.emails = []
    db.commit()
    again = {i["contact"]["name"]: i for i in client.get("/crm/queue").json()["items"]}
    assert again["Grey Consultants"]["channel"] == "find_contact"
    assert again["Grey Consultants"]["channel_target"] is None
    assert "Instagram" in again["Grey Consultants"]["channel_hint"]


def test_contact_filters(env):
    client, *_ = env
    a = client.get("/crm/contacts?tier=A&has_contact=true").json()
    assert {c["tier"] for c in a} == {"A"}
    assert all("Dead Co" not in c["name"] for c in a)   # excluded by default
