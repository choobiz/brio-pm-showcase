"""Find a document by name or by what a summary says about it.

⚠️ WHY THIS EXISTS. There was no document search of any kind. `GET /documents`
filtered on `project_id` and nothing else, so on the largest project — 4,019
files — finding a named drawing meant scrolling. That is the single most-cited
gap in the workspace proposal, and it is a plain product gap: no agent is
required to fix it, and the fix is worth having either way.

RANKING, and why it is not a relevance score. With a corpus this size (5,333
rows) a sequential scan costs nothing, so this uses explicit ordered tiers
rather than a tsvector. Tiers are predictable and explainable — "it matched the
filename exactly" is something the interface can state — whereas a similarity
score is neither. If the corpus grows an order of magnitude, add a generated
tsvector column; do not silently swap the ordering for a score nobody can read.

⚠️ "LATEST" IS UPLOAD ORDER, NOT REVISION ORDER. There is no revision chain on
`documents` — Rev C and Rev B are unrelated rows and the revision lives only in
a filename. So the newest upload that matches is the best available answer, and
callers must say so rather than implying the set is revision-aware. Real
revision handling is Phase 2.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

#: Matched on the filename, exactly / by prefix / anywhere, then on summary text.
#: Lower sorts first.
TIER_EXACT, TIER_PREFIX, TIER_FILENAME, TIER_SUMMARY = 0, 1, 2, 3

TIER_REASON = {
    TIER_EXACT: "name matches exactly",
    TIER_PREFIX: "name starts with this",
    TIER_FILENAME: "name contains this",
    TIER_SUMMARY: "the summary mentions this",
}

MAX_TERMS = 8


def _terms(q: str) -> list[str]:
    """Whitespace-split, deduplicated, capped.

    Capped because every term becomes two ILIKE predicates; an accidental paste
    of a whole paragraph should degrade to a narrow search, not a slow one.
    """
    seen: list[str] = []
    for t in (q or "").split():
        t = t.strip().lower()
        if t and t not in seen:
            seen.append(t)
        if len(seen) >= MAX_TERMS:
            break
    return seen


def search_documents(
    db: Session,
    *,
    query: str,
    project_ids: list[UUID],
    category: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Ranked matches, newest first within each tier.

    ``project_ids`` is the caller's already-authorised list. An EMPTY list
    returns nothing rather than everything — the common failure mode for a
    scoping parameter is to treat empty as unfiltered, and here that would be a
    cross-project leak.
    """
    terms = _terms(query)
    if not terms or not project_ids:
        return []

    params: dict[str, Any] = {
        "pids": [str(p) for p in project_ids],
        "lim": int(limit),
        "whole": query.strip().lower(),
        "prefix": query.strip().lower() + "%",
    }
    # Every term must appear somewhere — AND, not OR. Searching "floor plan"
    # should not return every file with "plan" in it.
    name_clauses, hay_clauses = [], []
    for i, t in enumerate(terms):
        params[f"t{i}"] = f"%{t}%"
        name_clauses.append(f"lower(d.filename) LIKE :t{i}")
        hay_clauses.append(
            f"(lower(d.filename) LIKE :t{i} OR lower(coalesce(s.summary_text,'')) LIKE :t{i})"
        )
    all_in_name = " AND ".join(name_clauses)
    all_in_hay = " AND ".join(hay_clauses)

    sql = f"""
        SELECT d.id, d.project_id, p.name AS project_name, d.filename, d.category,
               d.mime, d.size_bytes, d.created_at,
               s.summary_text,
               CASE
                 WHEN lower(d.filename) = :whole                THEN {TIER_EXACT}
                 WHEN lower(d.filename) LIKE :prefix            THEN {TIER_PREFIX}
                 WHEN {all_in_name}                             THEN {TIER_FILENAME}
                 ELSE {TIER_SUMMARY}
               END AS tier
          FROM documents d
          JOIN projects p ON p.id = d.project_id
     LEFT JOIN document_summaries s ON s.document_id = d.id
         WHERE d.project_id = ANY(:pids)
           AND d.archived = false
           AND d.size_bytes IS NOT NULL
           AND ({all_in_hay})
           {"AND d.category = :cat" if category else ""}
      ORDER BY tier ASC, d.created_at DESC
         LIMIT :lim
    """
    if category:
        params["cat"] = category

    rows = db.execute(_T(sql), params).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "project_id": str(r["project_id"]),
            "project_name": r["project_name"],
            "filename": r["filename"],
            "category": r["category"],
            "mime": r["mime"],
            "size_bytes": r["size_bytes"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "matched_on": TIER_REASON[int(r["tier"])],
            "snippet": _snippet(r["summary_text"], terms) if int(r["tier"]) == TIER_SUMMARY else None,
        }
        for r in rows
    ]


def _snippet(text: str | None, terms: list[str], width: int = 140) -> str | None:
    """A window around the first matching term, so a summary hit shows its
    evidence instead of asking the reader to take it on trust."""
    if not text:
        return None
    low = text.lower()
    pos = next((low.find(t) for t in terms if low.find(t) >= 0), -1)
    if pos < 0:
        return text[:width].strip() or None
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


# ── inside the files ──────────────────────────────────────────────────────
#
# A fifth tier, below every name and summary match: the words appear in the
# text read out of the file (services/document_text_index). Postgres full-text
# search, so "windows" finds "window" and word order does not matter; quoted
# phrases and -exclusions work as they do in a web search box.
#
# Within the tier, files are ordered by how many of their pages match, then by
# Postgres's own ranking of the best page. Both are things the page can state —
# "on 7 pages, best match page 12" — which keeps to the rule above: no bare
# score nobody can read.

#: Marks the matched words in a snippet. Characters that do not occur in the
#: corpus, so the page can split on them without escaping anything.
HIT_START, HIT_END = "⟦", "⟧"


def search_document_pages(
    db: Session,
    *,
    query: str,
    project_ids: list[UUID],
    category: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Files whose text matches, one row per file, citing its best page."""
    if not (query or "").strip() or not project_ids:
        return []
    params: dict[str, Any] = {
        "q": query.strip()[:500],
        "pids": [str(p) for p in project_ids],
        "lim": int(limit),
        "opts": (f"StartSel={HIT_START}, StopSel={HIT_END}, MaxWords=28, MinWords=10, "
                 "ShortWord=2, MaxFragments=1, FragmentDelimiter= … "),
    }
    if category:
        params["cat"] = category
    sql = f"""
        WITH q AS (SELECT websearch_to_tsquery('english', :q) AS tsq),
        hits AS (
            SELECT p.document_id, p.page_no, p.text,
                   ts_rank(p.tsv, q.tsq) AS rank,
                   count(*) OVER (PARTITION BY p.document_id) AS pages_matched
              FROM document_text_pages p
              JOIN documents d ON d.id = p.document_id
             CROSS JOIN q
             WHERE p.project_id = ANY(:pids)
               AND d.archived = false
               AND p.tsv @@ q.tsq
               {"AND d.category = :cat" if category else ""}
        ),
        best AS (
            SELECT DISTINCT ON (document_id) document_id, page_no, text, rank, pages_matched
              FROM hits
             ORDER BY document_id, rank DESC, page_no
        ),
        top AS (
            SELECT * FROM best ORDER BY pages_matched DESC, rank DESC LIMIT :lim
        )
        SELECT d.id, d.project_id, pr.name AS project_name, d.filename, d.category,
               d.mime, d.size_bytes, d.created_at,
               top.page_no, top.pages_matched,
               ts_headline('english', top.text, q.tsq, :opts) AS snippet
          FROM top
          JOIN documents d ON d.id = top.document_id
          JOIN projects pr ON pr.id = d.project_id
         CROSS JOIN q
         ORDER BY top.pages_matched DESC, top.rank DESC
    """
    rows = db.execute(_T(sql), params).mappings().all()
    out = []
    for r in rows:
        n = int(r["pages_matched"])
        # A Word file or a spreadsheet is stored as one "page"; citing page 1
        # of it would send someone looking for a page that is not there.
        paged = (r["filename"] or "").lower().endswith(".pdf")
        if not paged:
            where = "found in the text"
        elif n == 1:
            where = f"found on page {r['page_no']}"
        else:
            where = f"found on {n} pages, best on page {r['page_no']}"
        out.append({
            "id": str(r["id"]),
            "project_id": str(r["project_id"]),
            "project_name": r["project_name"],
            "filename": r["filename"],
            "category": r["category"],
            "mime": r["mime"],
            "size_bytes": r["size_bytes"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "matched_on": where,
            "page": int(r["page_no"]) if paged else None,
            "pages_matched": n if paged else None,
            "snippet": " ".join((r["snippet"] or "").split()) or None,
        })
    return out
