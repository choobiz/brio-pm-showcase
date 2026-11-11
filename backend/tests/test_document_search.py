"""Document search — the gap that made a 4,019-file project unnavigable.

The isolation tests here are not ceremony. Search is the first surface that
queries ACROSS projects, so it is the first place a scoping mistake becomes a
cross-project leak rather than a 403.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.document_search import search_documents, _terms, _snippet  # noqa: E402


def _project(db, name):
    pid = uuid4()
    db.execute(_T("insert into projects (id, name, slug, status) "
                  "values (:i, :n, :s, 'preconstruction')"),
               {"i": str(pid), "n": name, "s": f"p-{str(pid)[:8]}"})
    return pid


def _doc(db, pid, filename, category="drawing", summary=None):
    did = uuid4()
    db.execute(_T("insert into documents (id, project_id, filename, r2_key, category, "
                  "phase, size_bytes, archived) "
                  "values (:i, :p, :f, :k, :c, 'design', 1024, false)"),
               {"i": str(did), "p": str(pid), "f": filename, "k": f"t/{did}", "c": category})
    if summary is not None:
        db.execute(_T("insert into document_summaries (id, document_id, project_id, "
                      "skill_version, status, summary_text, structured_fields) "
                      "values (:i, :d, :p, 'document_summary@1', 'succeeded', :s, '{}'::jsonb)"),
                   {"i": str(uuid4()), "d": str(did), "p": str(pid), "s": summary})
    return did


# ── ranking ───────────────────────────────────────────────────────────────

def test_filename_beats_summary(db_session):
    db = db_session
    pid = _project(db, "Ranking")
    summary_only = _doc(db, pid, "S2.01 Foundation.pdf",
                        summary="Shows the floor plan layout for level 2.")
    by_name = _doc(db, pid, "A1.04 Floor Plan.pdf")
    db.flush()

    out = search_documents(db, query="floor plan", project_ids=[pid])
    assert [r["id"] for r in out] == [str(by_name), str(summary_only)], \
        "a filename match must outrank a summary match"
    assert out[0]["matched_on"] == "name contains this"
    assert out[1]["matched_on"] == "the summary mentions this"


def test_an_exact_name_wins_over_a_newer_partial_match(db_session):
    db = db_session
    pid = _project(db, "Exact")
    exact = _doc(db, pid, "plan.pdf")
    _doc(db, pid, "plan revision c.pdf")          # inserted later = newer
    db.flush()

    out = search_documents(db, query="plan.pdf", project_ids=[pid])
    assert out[0]["id"] == str(exact)


def test_every_term_must_match_not_just_one(db_session):
    """Searching 'floor plan' should not return every file containing 'plan'."""
    db = db_session
    pid = _project(db, "AndTerms")
    _doc(db, pid, "Site Plan.pdf")
    both = _doc(db, pid, "Ground Floor Plan.pdf")
    db.flush()

    out = search_documents(db, query="floor plan", project_ids=[pid])
    assert [r["id"] for r in out] == [str(both)]


def test_newest_first_within_a_tier(db_session):
    db = db_session
    pid = _project(db, "Recency")
    older = _doc(db, pid, "A1.04 Floor Plan rev A.pdf")
    newer = _doc(db, pid, "A1.04 Floor Plan rev B.pdf")
    db.execute(_T("update documents set created_at = now() - interval '2 days' where id = :i"),
               {"i": str(older)})
    db.flush()

    out = search_documents(db, query="floor plan", project_ids=[pid])
    assert out[0]["id"] == str(newer), "within a tier the newest upload comes first"


# ── project isolation — the one property with no acceptable failure rate ──

def test_a_project_you_cannot_see_never_appears(db_session):
    db = db_session
    mine, theirs = _project(db, "Mine"), _project(db, "Theirs")
    _doc(db, mine, "A1.04 Floor Plan.pdf")
    _doc(db, theirs, "A1.04 Floor Plan.pdf")
    db.flush()

    out = search_documents(db, query="floor plan", project_ids=[mine])
    assert len(out) == 1 and out[0]["project_id"] == str(mine)


def test_an_empty_scope_returns_nothing_not_everything(db_session):
    """The classic scoping bug: treating an empty filter as 'no filter'."""
    db = db_session
    pid = _project(db, "EmptyScope")
    _doc(db, pid, "A1.04 Floor Plan.pdf")
    db.flush()

    assert search_documents(db, query="floor", project_ids=[]) == []


# ── hygiene ───────────────────────────────────────────────────────────────

def test_archived_and_never_uploaded_rows_are_excluded(db_session):
    db = db_session
    pid = _project(db, "Hygiene")
    arch = _doc(db, pid, "A1.04 Floor Plan archived.pdf")
    db.execute(_T("update documents set archived = true where id = :i"), {"i": str(arch)})
    pending = _doc(db, pid, "A1.04 Floor Plan pending.pdf")
    db.execute(_T("update documents set size_bytes = null where id = :i"), {"i": str(pending)})
    live = _doc(db, pid, "A1.04 Floor Plan live.pdf")
    db.flush()

    out = search_documents(db, query="floor plan", project_ids=[pid])
    assert [r["id"] for r in out] == [str(live)]


def test_a_summary_hit_shows_its_evidence(db_session):
    db = db_session
    pid = _project(db, "Snippet")
    _doc(db, pid, "S2.01.pdf",
         summary="Foundation notes. " + "x" * 200 + " asbestos survey required before demolition.")
    db.flush()

    out = search_documents(db, query="asbestos", project_ids=[pid])
    assert len(out) == 1
    assert "asbestos" in (out[0]["snippet"] or "").lower(), \
        "a summary match must show the text it matched, not ask for trust"


def test_a_pasted_paragraph_degrades_to_a_narrow_search_not_a_slow_one():
    assert len(_terms(" ".join(f"w{i}" for i in range(50)))) == 8


def test_terms_are_deduplicated():
    assert _terms("plan Plan  PLAN floor") == ["plan", "floor"]


def test_no_query_matches_nothing(db_session):
    db = db_session
    pid = _project(db, "Blank")
    _doc(db, pid, "A1.04 Floor Plan.pdf")
    db.flush()
    assert search_documents(db, query="   ", project_ids=[pid]) == []
