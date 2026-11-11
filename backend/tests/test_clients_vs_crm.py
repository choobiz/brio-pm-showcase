"""Clients and CRM prospects share one table but must never share a page.

The bug (found 2026-10-27): the permit-prospect import tested `c.kind != "client"`
after find_or_create_client had already flushed the new row with the column
default "client", so it never relabelled anything. All 4,857 imported firms
landed as clients, the Clients page and the project picker listed them, and the
CRM contacts list (which filters kind = 'prospect') was empty.

The CRM tests seeded kind="prospect" by hand, so they stayed green throughout.
These tests run the real import and the real endpoints instead.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import CurrentUser  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.routers.clients import find_or_create, list_clients  # noqa: E402
from app.routers.projects import create_project, update_project  # noqa: E402
from app.schemas.client import ClientFindOrCreate  # noqa: E402
from app.schemas.project import ProjectCreateRequest, ProjectUpdate  # noqa: E402
from app.services import clients as svc  # noqa: E402


def _admin(db):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, 'superadmin')"),
               {"i": str(uid), "e": f"a-{str(uid)[:8]}@example.com"})
    return CurrentUser(id=str(uid), email="a@example.com", role="superadmin")


def _prospect(db, name="Permit Firm Ltd."):
    c = Client(name=name, normalized_name=name.lower(), kind="prospect",
               segment="gc", tier="C")
    db.add(c)
    db.flush()
    return c


def test_import_labels_new_firms_as_prospects_and_leaves_real_clients(
        db_session, tmp_path, monkeypatch):
    real = svc.find_or_create_client(db_session, name="Ben Lee",
                                     email="ben@example.com").client
    assert real.kind == "client"

    (tmp_path / "2026-10-01-targets.json").write_text(json.dumps([
        {"firm": "Brand New Builders Ltd.", "role": "gc", "tier": "C"},
        {"firm": "Ben Lee", "email": "ben@example.com", "role": "design", "tier": "D"},
    ]))
    import app.db
    import scripts.import_crm_targets as imp
    monkeypatch.setattr(app.db, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(sys, "argv", ["import_crm_targets", "--apply",
                                      "--source", str(tmp_path)])
    imp.main()

    new = db_session.query(Client).filter_by(normalized_name="brand new builders ltd.").one()
    assert new.kind == "prospect"
    # the script closes its session, so read the row again rather than refresh
    again = db_session.query(Client).filter_by(normalized_name="ben lee").one()
    assert again.kind == "client"  # matched, never downgraded


def test_clients_list_excludes_crm_prospects(db_session):
    _prospect(db_session, "Permit Firm Ltd.")
    svc.find_or_create_client(db_session, name="Real Client")
    names = [r.name for r in list_clients(q=None, db=db_session, _=_admin(db_session))]
    assert names == ["Real Client"]
    # and the search the project picker uses
    assert list_clients(q="permit", db=db_session, _=_admin(db_session)) == []


def test_find_or_create_turns_a_matched_prospect_into_a_client(db_session):
    p = _prospect(db_session, "Permit Firm Ltd.")
    out = find_or_create(ClientFindOrCreate(name="Permit Firm Ltd."),
                         db=db_session, _=_admin(db_session))
    assert out.client.id == p.id  # same record, history kept
    db_session.refresh(p)
    assert p.kind == "client"


def test_linking_a_prospect_to_a_project_makes_it_a_client(db_session):
    user = _admin(db_session)
    a = _prospect(db_session, "Firm A")
    b = _prospect(db_session, "Firm B")
    proj = create_project(ProjectCreateRequest(
        name="Kitchen", type="residential_reno", client_id=a.id, building_age=1975),
        db=db_session, user=user)
    db_session.refresh(a)
    assert a.kind == "client"

    update_project(proj.id, ProjectUpdate(client_id=b.id), db=db_session, user=user)
    db_session.refresh(b)
    assert b.kind == "client"
