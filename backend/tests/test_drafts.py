"""Drafts: what is sitting unissued, and taking one back safely.

⚠️ TEST-SCHEMA NOTE. `estimate_versions` is a STUB model — migrations own its
real schema and `create_all` builds a table with only `id`. `record_revisions`
is not mapped at all. Both are created here with the shape the service actually
queries, rather than by changing a conftest 629 other tests depend on. This is
the second time this divergence has bitten; it is a property of the suite, not
of this module.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import drafts as D  # noqa: E402


@pytest.fixture(autouse=True)
def _raw_tables(db_session):
    db_session.execute(_T("""
        CREATE TABLE IF NOT EXISTS record_revisions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id uuid, table_name text NOT NULL, row_id uuid NOT NULL,
            revision integer NOT NULL, payload jsonb NOT NULL, action text NOT NULL,
            reason text, actor_id uuid,
            created_at timestamptz NOT NULL DEFAULT now())"""))
    # The stub model gives us only `id`; add what the service reads.
    for ddl in (
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS project_id uuid",
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS is_active boolean DEFAULT false",
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS reference text",
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS version_number integer DEFAULT 1",
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS payload jsonb DEFAULT '{}'::jsonb",
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS ai_job_id uuid",
        "ALTER TABLE estimate_versions ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now()",
        "ALTER TABLE change_orders ADD COLUMN IF NOT EXISTS ai_job_id uuid",
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS ai_job_id uuid",
    ):
        db_session.execute(_T(ddl))
    db_session.flush()


def _project(db, name="Drafts"):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) "
                  "values (:i, :n, :s, 'preconstruction')"),
               {"i": str(pid), "n": name, "s": f"df-{str(pid)[:8]}"})
    return pid


_numbers = itertools.count(1)


def _next_number():
    # change_orders.number is an INTEGER, not a label.
    return next(_numbers)


def _co(db, pid, *, status="draft", title="Extra framing", ai_job=None):
    cid = uuid4()
    db.execute(_T("""insert into change_orders
        (id, project_id, number, title, cost_impact, schedule_impact_days, status, ai_job_id)
        values (:i, :p, :n, :t, 0, 0, :s, :a)"""),
        {"i": str(cid), "p": str(pid), "n": _next_number(), "t": title,
         "s": status, "a": str(ai_job) if ai_job else None})
    return cid


def _ev(db, pid, *, active=False, ref="EST-001", ai_job=None):
    eid = uuid4()
    db.execute(_T("""insert into estimate_versions
        (id, project_id, is_active, reference, version_number, payload, ai_job_id)
        values (:i, :p, :a, :r, 1, cast(:d as jsonb), :j)"""),
        {"i": str(eid), "p": str(pid), "a": active, "r": ref,
         "d": json.dumps({"title": "x"}), "j": str(ai_job) if ai_job else None})
    return eid


# --- what is unissued -------------------------------------------------------

def test_lists_unissued_across_every_record_type(db_session):
    pid = _project(db_session)
    _co(db_session, pid, status="draft")
    _co(db_session, pid, status="approved")      # decided — not a draft
    _ev(db_session, pid, active=False)
    _ev(db_session, pid, active=True)            # the active one — not a draft

    out = D.list_drafts(db_session, pid)
    kinds = sorted(i["kind"] for i in out["items"])
    assert kinds == ["change_order", "estimate_version"]
    assert out["total"] == 2


def test_draft_means_a_different_column_on_each_table(db_session):
    """An estimate version is unissued when is_active is false; a change order
    when status is 'draft'. A reviewer should not have to know that."""
    assert D.DRAFTABLE["estimate_version"]["where"] == "is_active = false"
    assert D.DRAFTABLE["change_order"]["where"] == "status = 'draft'"
    assert D.DRAFTABLE["invoice"]["where"] == "status = 'draft'"


# --- provenance -------------------------------------------------------------

def test_missing_provenance_reads_as_not_recorded_never_as_human(db_session):
    """The headline discipline. Every row predating 2026-10-02 is NULL because
    the link was never captured — not because a person wrote it."""
    pid = _project(db_session)
    _co(db_session, pid, title="old one", ai_job=None)
    out = D.list_drafts(db_session, pid)
    item = next(i for i in out["items"] if i["title"] == "old one")
    assert item["drafted_by"] == "not recorded"
    assert item["drafted_by"] != "human"
    assert "not a statement that a person wrote them" in out["untraced_note"]


def test_a_recorded_job_is_reported_as_ai_drafted(db_session):
    pid = _project(db_session)
    job = uuid4()
    db_session.execute(_T("insert into ai_jobs (id, project_id, kind, status) "
                          "values (:i, :p, 'ai_edit', 'done')"),
                       {"i": str(job), "p": str(pid)})
    _co(db_session, pid, title="new one", ai_job=job)
    out = D.list_drafts(db_session, pid)
    item = next(i for i in out["items"] if i["title"] == "new one")
    assert item["drafted_by"] == "ai"
    assert item["ai_job_id"] == str(job)
    assert out["traced"] == 1


# --- discarding -------------------------------------------------------------

def test_discarding_a_draft_removes_it_and_keeps_it_restorable(db_session):
    pid = _project(db_session)
    cid = _co(db_session, pid)
    out = D.delete_draft(db_session, "change_order", cid, user_id=None, reason="wrong job")
    assert out["deleted"] is True
    assert out["revision_number"] is not None, "discarded without a way back"

    gone = db_session.execute(
        _T("select count(*) from change_orders where id = :i"), {"i": str(cid)}).scalar()
    assert gone == 0
    kept = db_session.execute(
        _T("select reason from record_revisions where row_id = :r"), {"r": str(cid)}).scalar()
    assert kept == "wrong job"


def test_an_issued_record_is_refused_with_a_reason(db_session):
    """An issued invoice is evidence of what was billed. Deleting one rewrites
    history, so the refusal explains itself rather than greying out."""
    pid = _project(db_session)
    cid = _co(db_session, pid, status="approved")
    with pytest.raises(D.DraftError) as e:
        D.delete_draft(db_session, "change_order", cid, user_id=None)
    assert "decided" in str(e.value).lower()
    assert db_session.execute(
        _T("select count(*) from change_orders where id = :i"), {"i": str(cid)}).scalar() == 1


def test_a_draft_something_depends_on_is_refused(db_session):
    """A scope built from an estimate version outlives the version. Removing it
    would leave the scope pointing at nothing."""
    pid = _project(db_session)
    eid = _ev(db_session, pid, active=False)
    db_session.execute(_T("insert into scopes (id, project_id, payload, based_on_estimate_id) "
                          "values (:i, :p, '{}'::jsonb, :e)"),
                       {"i": str(uuid4()), "p": str(pid), "e": str(eid)})
    with pytest.raises(D.DraftError) as e:
        D.delete_draft(db_session, "estimate_version", eid, user_id=None)
    assert "built from this" in str(e.value)
    assert db_session.execute(
        _T("select count(*) from estimate_versions where id = :i"), {"i": str(eid)}).scalar() == 1


def test_a_missing_record_is_distinguished_from_an_issued_one(db_session):
    _project(db_session)
    with pytest.raises(D.DraftError) as e:
        D.delete_draft(db_session, "change_order", uuid4(), user_id=None)
    assert "no longer here" in str(e.value)


def test_an_unknown_kind_is_refused(db_session):
    with pytest.raises(D.DraftError):
        D.delete_draft(db_session, "projects", uuid4(), user_id=None)
