"""Find-or-create + merge integration tests for the client/CRM service.

These tests need Postgres because the schema uses native ARRAY columns —
they auto-skip via conftest.db_engine if Postgres is not reachable.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.client import Client
from app.models.project import Project
from app.services import clients as svc


# ── (a) new email creates new client ──────────────────────────────────────


def test_find_or_create_new_email_creates_client(db_session):
    result = svc.find_or_create_client(
        db_session,
        name="Sample Client",
        email="client@mail.example",
        phone="604-555-0181",
        address="1500 Birch St #1201, Vancouver BC",
    )
    assert result.created is True
    assert result.client.normalized_name == "sample client"
    assert result.client.normalized_emails == ["client@mail.example"]
    assert result.client.normalized_phones == ["6045550181"]
    assert result.client.email == "client@mail.example"  # singular mirror


# ── (b) same email matches and merges new phone ───────────────────────────


def test_find_or_create_email_match_merges_new_phone(db_session):
    svc.find_or_create_client(
        db_session,
        name="Sample Client",
        email="client@mail.example",
        phone="604-555-0181",
    )

    result = svc.find_or_create_client(
        db_session,
        name="Sam C.",
        email="client@mail.example",
        phone="604-555-2222",  # new phone for the same person
    )
    assert result.created is False
    assert sorted(result.client.normalized_phones) == sorted(["6045550181", "6045552222"])
    # Original normalized_name stays — we don't overwrite identity on match
    assert result.client.normalized_name == "sample client"


# ── (c) priority: email > phone > name ────────────────────────────────────


def test_find_or_create_priority_email_beats_phone_beats_name(db_session):
    # Two distinct clients in the DB
    a = svc.find_or_create_client(
        db_session,
        name="Sample Client",
        email="m@example.com",
        phone="111-111-1111",
    )
    b = svc.find_or_create_client(
        db_session,
        name="Mary Client",
        email="m2@example.com",
        phone="222-222-2222",
    )
    db_session.flush()

    # Phone matches A, email matches B → email wins.
    result = svc.find_or_create_client(
        db_session,
        name="Mary Client",
        email="m2@example.com",
        phone="111-111-1111",
    )
    assert result.created is False
    assert result.client.id == b.client.id

    # Name matches A, phone matches B → phone wins.
    result2 = svc.find_or_create_client(
        db_session,
        name="Sample Client",
        phone="222-222-2222",
    )
    assert result2.created is False
    assert result2.client.id == b.client.id

    # Only name matches → returns A.
    result3 = svc.find_or_create_client(db_session, name="sample  client")
    assert result3.created is False
    assert result3.client.id == a.client.id


# ── (d) normalization (covered in test_normalization.py too, smoke here) ─


def test_normalization_smoke():
    assert svc.normalize_phone("604-555-0181") == "6045550181"
    assert svc.normalize_email("Foo@Bar.COM") == "foo@bar.com"
    assert svc.normalize_name("  Sample  Client ") == "sample client"


# ── (e) merge clients ────────────────────────────────────────────────────


def test_merge_moves_projects_and_dedups_arrays(db_session):
    # Target: Sam (1 project)
    a = svc.find_or_create_client(
        db_session,
        name="Sample Client",
        email="client@mail.example",
        phone="604-555-0181",
    ).client
    db_session.add(Project(slug="proj-a-1", name="Birch Reno", client_id=a.id))

    # Source: Sam-duplicate (2 projects)
    b = svc.find_or_create_client(
        db_session,
        name="Mary Client",
        email="client.alt@example.com",
        phone="604-555-2222",
    ).client
    db_session.add(Project(slug="proj-b-1", name="Harbour Cottage", client_id=b.id))
    db_session.add(Project(slug="proj-b-2", name="Banbury Add-on", client_id=b.id))
    db_session.flush()

    target = svc.merge_clients(db_session, source_id=b.id, target_id=a.id)
    db_session.flush()

    # Target should now hold all 3 projects
    project_count = db_session.execute(
        select(Project).where(Project.client_id == a.id)
    ).scalars().all()
    assert len(project_count) == 3

    # Source is deleted
    assert db_session.get(Client, b.id) is None

    # Arrays merged + deduped
    assert sorted(target.normalized_emails) == sorted(["client@mail.example", "client.alt@example.com"])
    assert sorted(target.normalized_phones) == sorted(["6045550181", "6045552222"])


def test_merge_self_raises(db_session):
    a = svc.find_or_create_client(db_session, name="Foo").client
    db_session.flush()
    with pytest.raises(ValueError):
        svc.merge_clients(db_session, source_id=a.id, target_id=a.id)


# ── extra: search ─────────────────────────────────────────────────────────


def test_search_finds_by_phone_substring(db_session):
    svc.find_or_create_client(
        db_session,
        name="Sample Client",
        email="client@mail.example",
        phone="604-555-0181",
    )
    svc.find_or_create_client(db_session, name="Alex Example", phone="604-555-0193")
    db_session.flush()

    rows = svc.search_clients(db_session, "0181")
    assert len(rows) == 1
    assert rows[0].name == "Sample Client"


def test_search_finds_by_name_substring(db_session):
    svc.find_or_create_client(db_session, name="Sample Client")
    svc.find_or_create_client(db_session, name="Alex Sample")
    db_session.flush()

    rows = svc.search_clients(db_session, "sample")
    assert {r.name for r in rows} == {"Sample Client", "Alex Sample"}
