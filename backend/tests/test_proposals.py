"""Proposals: what was suggested, what it would touch, and what was decided.

The acceptance criterion from §11 is the headline here: "Reject a proposal; who
proposed, what it would have changed, who rejected and why are all retrievable.
All four present." Before this table existed, declining an AI edit left no trace
at all, so all four were absent and nothing said so.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.proposal import Proposal  # noqa: E402
from app.services import proposals as P  # noqa: E402


@pytest.fixture(autouse=True)
def _raw_tables(db_session):
    """`record_revisions` is a RAW table — created by migration 0052, not by
    `Base.metadata.create_all()`, because nothing maps it. The shared conftest
    builds the test schema from the models, so it is absent here.

    Created locally rather than by changing conftest, which 616 other tests
    depend on. Shape copied from 0052_record_revisions.py.

    Worth recording: no test in this suite exercised `snapshot()` before now.
    The service that stops an AI rewriting a whole scope had no coverage at all.
    """
    db_session.execute(_T("""
        CREATE TABLE IF NOT EXISTS record_revisions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id uuid,
            table_name text NOT NULL,
            row_id uuid NOT NULL,
            revision integer NOT NULL,
            payload jsonb NOT NULL,
            action text NOT NULL,
            reason text,
            actor_id uuid,
            created_at timestamptz NOT NULL DEFAULT now()
        )"""))
    db_session.flush()


def _project(db, name="Proposals"):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) "
                  "values (:i, :n, :s, 'preconstruction')"),
               {"i": str(pid), "n": name, "s": f"pr-{str(pid)[:8]}"})
    return pid


def _user(db, email="tester@builtbybrio.com"):
    uid = uuid4()
    db.execute(_T("insert into users (id, email, role) values (:i, :e, 'superadmin')"),
               {"i": str(uid), "e": f"{str(uid)[:8]}-{email}"})
    return uid


def _scope(db, pid, payload=None):
    sid = uuid4()
    import json
    db.execute(_T("insert into scopes (id, project_id, payload) "
                  "values (:i, :p, cast(:d as jsonb))"),
               {"i": str(sid), "p": str(pid),
                "d": json.dumps(payload or {"title": "Original title",
                                            "assumptions": ["one"]})})
    return sid


def _change(path, before, after, action="replace"):
    # `action` is required by edit_plan.verify — a change without one is marked
    # inapplicable with "unknown action ''".
    return {"path": path, "before": before, "after": after, "action": action}


def _make(db, pid, sid, **kw):
    kw.setdefault("kind", "scope")
    kw.setdefault("summary", "Retitle the scope")
    kw.setdefault("change_set", [_change("title", "Original title", "New title")])
    kw.setdefault("origin", "ai_edit")
    return P.create(db, project_id=pid, target_id=sid, **kw)


# --- the record itself ------------------------------------------------------

def test_a_proposal_writes_nothing_to_the_target(db_session):
    pid = _project(db_session)
    sid = _scope(db_session, pid)
    _make(db_session, pid, sid)
    title = db_session.execute(
        _T("select payload->>'title' from scopes where id = :i"), {"i": str(sid)}).scalar()
    assert title == "Original title", "proposing changed the record"


def test_an_unknown_target_kind_is_refused(db_session):
    pid = _project(db_session)
    with pytest.raises(P.ProposalError):
        P.create(db_session, project_id=pid, kind="projects", target_id=uuid4(),
                 summary="x", change_set=[], origin="ai_edit")


def test_a_proposal_needs_a_summary(db_session):
    pid = _project(db_session)
    sid = _scope(db_session, pid)
    with pytest.raises(P.ProposalError):
        _make(db_session, pid, sid, summary="   ")


# --- the acceptance criterion ----------------------------------------------

def test_rejection_retains_all_four_facts(db_session):
    """Who proposed · what it would have changed · who rejected · why."""
    pid = _project(db_session)
    sid = _scope(db_session, pid)
    author, decider = _user(db_session, "author@x.com"), _user(db_session, "boss@x.com")

    p = _make(db_session, pid, sid, proposed_by_id=author)
    P.reject(db_session, p, user_id=decider, reason="The client asked for the old wording.")

    row = db_session.get(Proposal, p.id)
    assert row.proposed_by_id == author                      # who proposed
    assert row.change_set[0]["after"] == "New title"         # what it would have changed
    assert row.decided_by_id == decider                      # who rejected
    assert row.decision_reason == "The client asked for the old wording."   # why
    assert row.decided_at is not None
    assert row.status == "rejected"


def test_a_rejection_without_a_reason_is_refused(db_session):
    """A rejection with no reason answers three of the four questions and
    leaves blank the one anybody would ask later."""
    pid = _project(db_session)
    sid = _scope(db_session, pid)
    p = _make(db_session, pid, sid)
    for empty in ("", "   ", None):
        with pytest.raises(P.ProposalError):
            P.reject(db_session, p, user_id=None, reason=empty)
    assert db_session.get(Proposal, p.id).status == "pending"


def test_a_rejected_proposal_survives_and_is_not_reopened(db_session):
    pid = _project(db_session)
    sid = _scope(db_session, pid)
    p = _make(db_session, pid, sid)
    P.reject(db_session, p, user_id=None, reason="not now")
    with pytest.raises(P.ProposalError):
        P.accept(db_session, p, user_id=None)
    with pytest.raises(P.ProposalError):
        P.reject(db_session, p, user_id=None, reason="again")
    assert db_session.get(Proposal, p.id) is not None, "a rejection must not be deleted"


# --- accepting --------------------------------------------------------------

def test_accepting_applies_and_snapshots_first(db_session):
    pid = _project(db_session)
    sid = _scope(db_session, pid)
    u = _user(db_session)
    p = _make(db_session, pid, sid)

    out = P.accept(db_session, p, user_id=u)
    assert out["applied"] == 1

    title = db_session.execute(
        _T("select payload->>'title' from scopes where id = :i"), {"i": str(sid)}).scalar()
    assert title == "New title"

    row = db_session.get(Proposal, p.id)
    assert row.status == "accepted"
    assert row.revision_number is not None, "accepted without a restorable snapshot"

    kept = db_session.execute(
        _T("select count(*) from record_revisions where table_name='scopes' and row_id=:r"),
        {"r": str(sid)}).scalar()
    assert kept >= 1, "the prior state was not snapshotted"


def test_a_change_whose_before_no_longer_matches_is_refused(db_session):
    """The record moved underneath the proposal. Guessing is worse than stopping."""
    pid = _project(db_session)
    sid = _scope(db_session, pid)
    p = _make(db_session, pid, sid)

    db_session.execute(
        _T("update scopes set payload = jsonb_set(payload,'{title}','\"Edited by hand\"') "
           "where id = :i"), {"i": str(sid)})

    with pytest.raises(P.ProposalError) as e:
        P.accept(db_session, p, user_id=None)
    assert "changed since" in str(e.value)
    assert db_session.get(Proposal, p.id).status == "stale"

    title = db_session.execute(
        _T("select payload->>'title' from scopes where id = :i"), {"i": str(sid)}).scalar()
    assert title == "Edited by hand", "a stale proposal overwrote a hand edit"


def test_unticking_a_change_means_something(db_session):
    pid = _project(db_session)
    sid = _scope(db_session, pid, {"title": "A", "assumptions": ["one"]})
    p = _make(db_session, pid, sid, change_set=[
        _change("title", "A", "B"),
        _change("assumptions[0]", "one", "two"),
    ])
    out = P.accept(db_session, p, user_id=None, accept_paths=["title"])
    assert out["applied"] == 1
    row = db_session.execute(
        _T("select payload from scopes where id = :i"), {"i": str(sid)}).scalar()
    assert row["title"] == "B"
    assert row["assumptions"][0] == "one", "an unticked change was applied anyway"


def test_accepting_nothing_is_distinguished_from_being_stale(db_session):
    pid = _project(db_session)
    sid = _scope(db_session, pid)
    p = _make(db_session, pid, sid)
    with pytest.raises(P.ProposalError) as e:
        P.accept(db_session, p, user_id=None, accept_paths=[])
    assert "selected" in str(e.value).lower()
    assert db_session.get(Proposal, p.id).status == "pending", "nothing picked != stale"


# --- superseding ------------------------------------------------------------

def test_an_earlier_pending_proposal_is_superseded_not_deleted(db_session):
    """Two pending proposals on one record is a trap: the second's `before`
    values were read from the document the first would have changed."""
    pid = _project(db_session)
    sid = _scope(db_session, pid)
    first = _make(db_session, pid, sid, summary="first")
    n = P.supersede_pending(db_session, pid, "scopes", sid)
    assert n == 1
    db_session.expire_all()
    assert db_session.get(Proposal, first.id).status == "superseded"


# --- impact -----------------------------------------------------------------

def test_impact_is_empty_when_nothing_references_the_record(db_session):
    pid = _project(db_session)
    sid = _scope(db_session, pid)
    p = _make(db_session, pid, sid)
    assert p.impact == [], "claimed a consequence that does not exist"


def test_dependents_are_read_from_the_schema_and_exclude_self_references(db_session):
    """⚠️ ASSERTS THE MECHANISM, NOT PRODUCTION'S SCHEMA.

    An earlier version of this test asserted that `schedules` and `contracts`
    appear among estimate_versions' dependents, because that is what production
    holds. It failed — and correctly: those are RAW tables that
    `create_all` never builds, so the test database cannot have them. Asserting
    production's shape from a test database is the same mistake in reverse as
    trusting a green suite about production, and it would have passed only by
    accident if conftest ever changed.

    What is asserted here is the behaviour: dependents come from
    information_schema, self-references are dropped, and the named exclusions
    hold. The production figure — nine dependents on estimate_versions, none on
    scopes — was measured directly and is recorded in the service docstring.
    """
    pid = _project(db_session)
    sid = _scope(db_session, pid)

    deps = P._dependents(db_session, "scopes")
    names = {c for c, _ in deps}
    assert "scopes" not in names, "a self-reference was reported as a dependent"
    assert "regeneration_approvals" not in names

    # estimate_versions is mapped, so whatever references it in THIS schema is
    # found by the same query that finds nine of them in production.
    ev_deps = P._dependents(db_session, "estimate_versions")
    assert isinstance(ev_deps, list)
    assert all(c != "estimate_versions" for c, _ in ev_deps)

    # Nothing points at this row, so nothing is claimed about it.
    assert P.impact_of(db_session, "scopes", sid) == []
    assert P.impact_of(db_session, "estimate_versions", uuid4()) == []
