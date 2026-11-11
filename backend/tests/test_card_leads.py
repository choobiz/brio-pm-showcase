"""Business-card leads: POST /public/card-leads + /crm/card-leads (Amir, 2026-10-20).

The public route is the only unauthenticated WRITE in the API, so its gates are
pinned here: the body-carried intake key, the honeypot, and the contact rule.

Most tests use a fake session (no database), so they run everywhere. The
round-trip tests at the bottom use ``db_session``, which skips without a
throwaway TEST_DATABASE_URL — see conftest.py.
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.auth as auth_mod
from app.auth import CurrentUser, get_current_user
from app.config import settings
from app.db import get_db
from app.main import app
from app.models.crm import CrmCardLead

KEY = "test-card-intake-key"
URL = "/public/card-leads"


class FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(settings, "card_intake_key", KEY)
    s = FakeSession()
    app.dependency_overrides[get_db] = lambda: s
    yield s, TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _body(**kw):
    b = {"intake_key": KEY, "name": "  Jane Dev ", "company": "Dev Co", "role": "developer",
         "phone": "604-555-0100", "email": "jane@devco.example", "note": "Townhomes in Burnaby",
         "card_slug": "amir", "source": "qr", "user_agent": "Mozilla/5.0"}
    b.update(kw)
    return b


def test_valid_submission_creates_a_row(fake):
    s, c = fake
    r = c.post(URL, json=_body())
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert len(s.added) == 1 and s.commits == 1
    lead = s.added[0]
    assert isinstance(lead, CrmCardLead)
    assert lead.name == "Jane Dev"           # trimmed
    assert lead.card_slug == "amir" and lead.status == "new" and lead.role == "developer"


@pytest.mark.parametrize("key", [None, "", "wrong-key", KEY + "x"])
def test_wrong_or_missing_key_is_403(fake, key):
    s, c = fake
    body = _body()
    if key is None:
        body.pop("intake_key")
    else:
        body["intake_key"] = key
    assert c.post(URL, json=body).status_code == 403
    assert s.added == []


def test_unset_env_key_refuses_everything(fake, monkeypatch):
    s, c = fake
    monkeypatch.setattr(settings, "card_intake_key", "")
    assert c.post(URL, json=_body(intake_key="")).status_code == 403
    assert s.added == []


def test_key_in_a_header_is_not_accepted(fake):
    """The proxy drops unknown headers; the key must travel in the body."""
    s, c = fake
    body = _body()
    body.pop("intake_key")
    assert c.post(URL, json=body, headers={"X-Intake-Key": KEY}).status_code == 403


@pytest.mark.parametrize("phone,email", [(None, None), ("", ""), ("   ", "  ")])
def test_no_phone_and_no_email_is_422(fake, phone, email):
    s, c = fake
    assert c.post(URL, json=_body(phone=phone, email=email)).status_code == 422
    assert s.added == []


def test_either_contact_field_alone_is_enough(fake):
    s, c = fake
    assert c.post(URL, json=_body(email=None)).status_code == 200
    assert c.post(URL, json=_body(phone="")).status_code == 200
    assert len(s.added) == 2


def test_honeypot_returns_ok_and_stores_nothing(fake):
    s, c = fake
    r = c.post(URL, json=_body(website="http://spam.example"))
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert s.added == [] and s.commits == 0


@pytest.mark.parametrize("field,value", [
    ("name", "x" * 121), ("company", "x" * 161), ("phone", "1" * 41),
    ("email", "a" * 195 + "@b.com"), ("note", "x" * 1001),
    ("email", "not-an-email"), ("name", "   "), ("role", "astronaut"), ("card_slug", ""),
])
def test_bad_fields_are_422(fake, field, value):
    s, c = fake
    assert c.post(URL, json=_body(**{field: value})).status_code == 422
    assert s.added == []


@pytest.mark.parametrize("role", ["manager", "worker", "owner"])
def test_crm_listing_is_superadmin_only(fake, role):
    s, c = fake
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(uuid4()), email=f"{role}@t.local", role=role)
    assert c.get("/crm/card-leads").status_code == 403
    assert c.patch(f"/crm/card-leads/{uuid4()}", json={"status": "contacted"}).status_code == 403


def test_crm_listing_needs_a_signed_in_user(fake, monkeypatch):
    s, c = fake
    monkeypatch.setattr(auth_mod, "_is_production", lambda: True)
    monkeypatch.setattr(auth_mod, "_is_secret_unset", lambda: False)
    assert c.get("/crm/card-leads").status_code == 401


# --- database round trip (skips without TEST_DATABASE_URL) -------------------

@pytest.fixture
def real(db_session, monkeypatch):
    monkeypatch.setattr(settings, "card_intake_key", KEY)
    db_session.execute(CrmCardLead.__table__.delete())
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=str(uuid4()), email="sa@t.local", role="superadmin")
    yield db_session, TestClient(app)
    app.dependency_overrides.clear()


def test_round_trip_create_list_contact(real):
    db, c = real
    assert c.post(URL, json=_body()).status_code == 200
    assert c.post(URL, json=_body(website="bot")).status_code == 200
    assert db.query(CrmCardLead).count() == 1

    rows = c.get("/crm/card-leads").json()
    assert len(rows) == 1 and rows[0]["name"] == "Jane Dev" and rows[0]["status"] == "new"

    r = c.patch(f"/crm/card-leads/{rows[0]['id']}", json={"status": "contacted"})
    assert r.status_code == 200 and r.json()["contacted_at"] is not None
    assert c.get("/crm/card-leads", params={"status": "new"}).json() == []
    assert c.patch(f"/crm/card-leads/{rows[0]['id']}", json={"status": "bogus"}).status_code == 422
