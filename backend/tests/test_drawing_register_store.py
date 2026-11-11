"""Confirming the register: what is stored, and what is refused.

The parser proposes; this is where a person's decision is recorded. Two
properties carry the weight:

  · CURRENT IS DERIVED, never stored — so adding a newer revision changes which
    one is in force without anything being rewritten;
  · CONFIRMING RE-CHECKS WHAT WAS SHOWN — a drawing uploaded between the page
    rendering and the button being pressed must not be silently swept into a
    grouping nobody looked at.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.drawing_register_store import (  # noqa: E402
    RegisterChanged,
    confirm_register,
    register_for_project,
)


def _project(db, name="Register"):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) "
                  "values (:i, :n, :s, 'preconstruction')"),
               {"i": str(pid), "n": name, "s": f"r-{str(pid)[:8]}"})
    return pid


def _drawing(db, pid, filename, size, *, minutes=0):
    did = uuid4()
    db.execute(_T("insert into documents (id, project_id, filename, r2_key, category, "
                  "phase, size_bytes, created_at) values (:i, :p, :f, :k, 'drawing', "
                  "'design', :s, now() + (:m || ' minutes')::interval)"),
               {"i": str(did), "p": str(pid), "f": filename, "k": f"t/{did}",
                "s": size, "m": str(minutes)})
    return did


def _user(db):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, 'manager')"),
               {"i": str(uid), "e": f"u-{str(uid)[:8]}@example.com"})
    return uid


def _sheet(reg, n=0):
    return reg["sheets"][n]


# ── before anything is confirmed ──────────────────────────────────────────

def test_everything_starts_as_a_proposal(db_session):
    db = db_session
    pid = _project(db)
    _drawing(db, pid, "… SMP (R2) Drawings.pdf", 4804232)
    _drawing(db, pid, "… SMP (R3) Drawings.pdf", 4792306)
    db.flush()

    reg = register_for_project(db, pid)
    assert reg["summary"]["proposed_drawings"] == 1
    assert reg["summary"]["confirmed_drawings"] == 0
    assert _sheet(reg)["status"] == "proposed"
    assert _sheet(reg)["reviewed_at"] is None


# ── confirming ────────────────────────────────────────────────────────────

def test_confirming_records_the_grouping_and_who_did_it(db_session):
    db = db_session
    pid, uid = _project(db), _user(db)
    _drawing(db, pid, "… SMP (R2) Drawings.pdf", 4804232)
    _drawing(db, pid, "… SMP (R3) Drawings.pdf", 4792306)
    db.flush()

    reg = register_for_project(db, pid)
    out = confirm_register(db, pid, reg["fingerprint"], uid)
    assert out == {"drawings_confirmed": 1, "files_recorded": 2}

    after = register_for_project(db, pid)
    s = _sheet(after)
    assert s["status"] == "confirmed"
    assert s["grouped_because_plain"] == "confirmed by a person"
    assert s["reviewed_at"] is not None
    assert [r["revision"] for r in s["revisions"]] == ["R3", "R2"]
    assert s["revisions"][0]["is_current"] is True


def test_the_group_is_shared_by_its_revisions(db_session):
    db = db_session
    pid, uid = _project(db), _user(db)
    _drawing(db, pid, "… SMP (R2) Drawings.pdf", 4804232)
    _drawing(db, pid, "… SMP (R3) Drawings.pdf", 4792306)
    db.flush()
    confirm_register(db, pid, register_for_project(db, pid)["fingerprint"], uid)

    gids = [r[0] for r in db.execute(_T(
        "select distinct drawing_group_id from documents where project_id = :p"),
        {"p": str(pid)}).fetchall()]
    assert len(gids) == 1 and gids[0] is not None


def test_a_duplicate_is_recorded_as_one_not_as_a_revision(db_session):
    db = db_session
    pid, uid = _project(db), _user(db)
    orig = _drawing(db, pid, "109 Maple_Issued for coordination 20230123.pdf", 3590229)
    copy = _drawing(db, pid, "Copy of 109 Maple_Issued for coordination 20230123.pdf", 3590229)
    db.flush()
    confirm_register(db, pid, register_for_project(db, pid)["fingerprint"], uid)

    row = db.execute(_T("select duplicate_of_id from documents where id = :i"),
                     {"i": str(copy)}).scalar()
    assert row == orig, "the copy points at the original"
    kept = db.execute(_T("select duplicate_of_id from documents where id = :i"),
                      {"i": str(orig)}).scalar()
    assert kept is None
    after = register_for_project(db, pid)
    assert after["summary"]["drawings_with_more_than_one_revision"] == 0
    assert len(after["duplicates"]) == 1


# ── current is derived ────────────────────────────────────────────────────

def test_a_newer_revision_takes_over_without_rewriting_the_group(db_session):
    """The reason there is no is_current column: adding R4 changes which is in
    force, and nothing about R2 or R3 has to be touched for that to be true."""
    db = db_session
    pid, uid = _project(db), _user(db)
    _drawing(db, pid, "… SMP (R2) Drawings.pdf", 4804232)
    _drawing(db, pid, "… SMP (R3) Drawings.pdf", 4792306)
    db.flush()
    confirm_register(db, pid, register_for_project(db, pid)["fingerprint"], uid)

    gid = db.execute(_T("select drawing_group_id from documents where project_id = :p limit 1"),
                     {"p": str(pid)}).scalar()
    newer = _drawing(db, pid, "… SMP (R4) Drawings.pdf", 4700000, minutes=5)
    db.execute(_T("update documents set drawing_group_id = :g, revision_label = 'R4' "
                  "where id = :i"), {"g": str(gid), "i": str(newer)})
    db.flush()

    s = _sheet(register_for_project(db, pid))
    assert s["revisions"][0]["revision"] == "R4" and s["revisions"][0]["is_current"] is True
    assert [r["is_current"] for r in s["revisions"]] == [True, False, False]


def test_an_issue_date_outranks_a_revision_label(db_session):
    """A drawing issued in November supersedes one issued in October whatever
    the labels say."""
    db = db_session
    pid = _project(db)
    gid = uuid4()
    a = _drawing(db, pid, "Plan Rev Z.pdf", 100)
    b = _drawing(db, pid, "Plan Rev A.pdf", 110)
    db.execute(_T("update documents set drawing_group_id = :g, revision_label = 'Rev Z', "
                  "issued_on = date '2025-10-28' where id = :i"), {"g": str(gid), "i": str(a)})
    db.execute(_T("update documents set drawing_group_id = :g, revision_label = 'Rev A', "
                  "issued_on = date '2025-11-06' where id = :i"), {"g": str(gid), "i": str(b)})
    db.flush()

    s = _sheet(register_for_project(db, pid))
    assert s["revisions"][0]["revision"] == "Rev A", "November beats October"


# ── the guard ─────────────────────────────────────────────────────────────

def test_a_drawing_uploaded_in_between_refuses_the_confirmation(db_session):
    """THE GUARD. Otherwise a file nobody saw is swept into a grouping nobody
    reviewed — which is exactly what confirming is meant to prevent."""
    db = db_session
    pid, uid = _project(db), _user(db)
    _drawing(db, pid, "… SMP (R2) Drawings.pdf", 4804232)
    db.flush()
    stale = register_for_project(db, pid)["fingerprint"]

    _drawing(db, pid, "… SMP (R3) Drawings.pdf", 4792306, minutes=1)
    db.flush()

    with pytest.raises(RegisterChanged) as e:
        confirm_register(db, pid, stale, uid)
    assert "changed since this was shown" in str(e.value)

    assert db.execute(_T("select count(*) from documents where project_id = :p "
                         "and drawing_group_id is not null"),
                      {"p": str(pid)}).scalar() == 0, "nothing was written"


def test_confirming_twice_is_a_no_op_not_a_second_group(db_session):
    db = db_session
    pid, uid = _project(db), _user(db)
    _drawing(db, pid, "… SMP (R2) Drawings.pdf", 4804232)
    _drawing(db, pid, "… SMP (R3) Drawings.pdf", 4792306)
    db.flush()
    confirm_register(db, pid, register_for_project(db, pid)["fingerprint"], uid)

    again = register_for_project(db, pid)
    out = confirm_register(db, pid, again["fingerprint"], uid)
    assert out["drawings_confirmed"] == 0, "nothing left to confirm"
    assert register_for_project(db, pid)["summary"]["confirmed_drawings"] == 1


def test_confirmed_and_proposed_coexist(db_session):
    db = db_session
    pid, uid = _project(db), _user(db)
    _drawing(db, pid, "… SMP (R2) Drawings.pdf", 4804232)
    _drawing(db, pid, "… SMP (R3) Drawings.pdf", 4792306)
    db.flush()
    confirm_register(db, pid, register_for_project(db, pid)["fingerprint"], uid)

    _drawing(db, pid, "Site Plan.pdf", 500, minutes=9)
    db.flush()
    reg = register_for_project(db, pid)
    assert reg["summary"]["confirmed_drawings"] == 1
    assert reg["summary"]["proposed_drawings"] == 1
    assert {s["status"] for s in reg["sheets"]} == {"confirmed", "proposed"}
