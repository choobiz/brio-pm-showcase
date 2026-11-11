"""A retried POST /invoices must not take a second number out of the sequence.

On 2026-10-06 one POST /invoices returned 201 and the browser showed an error:
the response was lost returning through the Vercel proxy, which calls fetch()
with no timeout and sets no maxDuration. A lost response looks exactly like a
failed create, so the natural reaction - press the button again - would have
issued a second invoice number for a duplicate. An abandoned number leaves a
hole in a billing record with nothing in it to explain the gap.

These exercise the database path rather than the handler in isolation, because
the protection is the UNIQUE index. The in-handler lookup is only the fast path,
and a test that stubbed the database would pass with the index missing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.invoice import Invoice  # noqa: E402


@pytest.fixture()
def project_id(db_session):
    pid = uuid4()
    db_session.execute(
        _T("INSERT INTO projects (id, name, slug) VALUES (:i, :n, :s)"),
        {"i": str(pid), "n": "Idempotency fixture", "s": f"idem-{pid.hex[:8]}"},
    )
    db_session.commit()
    return pid


def _make(db_session, project_id, *, number: str, key: str | None) -> Invoice:
    inv = Invoice(
        project_id=project_id, number=number, type="progress",
        payload={"total": 100.0}, status="draft", idempotency_key=key,
    )
    db_session.add(inv)
    db_session.commit()
    return inv


def test_the_column_exists_and_holds_a_key(db_session, project_id):
    inv = _make(db_session, project_id, number="INV-TEST-0001", key="key-aaa")
    assert inv.idempotency_key == "key-aaa"


def test_the_same_key_cannot_be_used_twice(db_session, project_id):
    """The index is the actual guarantee. Without it the handler's lookup is a
    race, and two submits a few milliseconds apart both create an invoice."""
    from sqlalchemy.exc import IntegrityError

    _make(db_session, project_id, number="INV-TEST-0002", key="key-bbb")
    with pytest.raises(IntegrityError):
        _make(db_session, project_id, number="INV-TEST-0003", key="key-bbb")
    db_session.rollback()


def test_many_invoices_may_carry_no_key(db_session, project_id):
    """Every invoice created before 2026-10-06 has none, and a unique index that
    rejected a second NULL would make the whole table unwritable."""
    _make(db_session, project_id, number="INV-TEST-0004", key=None)
    _make(db_session, project_id, number="INV-TEST-0005", key=None)
    n = db_session.execute(
        _T("SELECT count(*) FROM invoices WHERE project_id = :p "
           "AND idempotency_key IS NULL"),
        {"p": str(project_id)},
    ).scalar()
    assert n == 2


def test_a_replay_finds_the_invoice_the_first_attempt_created(db_session, project_id):
    from app.routers.invoices import _replay

    first = _make(db_session, project_id, number="INV-TEST-0006", key="key-ccc")
    found = _replay(db_session, "key-ccc", project_id)
    assert found is not None
    assert found.id == first.id
    assert found.number == "INV-TEST-0006"   # the SAME number, not a new one


def test_an_unseen_key_is_not_a_replay(db_session, project_id):
    from app.routers.invoices import _replay

    assert _replay(db_session, "key-never-used", project_id) is None


def test_a_key_belonging_to_another_project_is_refused_not_returned(db_session, project_id):
    """Returning it would hand this project's caller another project's invoice."""
    from fastapi import HTTPException

    from app.routers.invoices import _replay

    other = uuid4()
    db_session.execute(
        _T("INSERT INTO projects (id, name, slug) VALUES (:i, :n, :s)"),
        {"i": str(other), "n": "Someone else", "s": f"other-{other.hex[:8]}"},
    )
    db_session.commit()
    _make(db_session, other, number="INV-TEST-0007", key="key-ddd")

    with pytest.raises(HTTPException) as e:
        _replay(db_session, "key-ddd", project_id)
    assert e.value.status_code == 409
