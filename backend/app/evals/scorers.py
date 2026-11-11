"""Deterministic scorers for each AI job kind.

A scorer takes the *parsed* model output and the golden `expect` block and
returns (score in [0,1], detail dict). Scorers are pure + dependency-free so
the harness runs in CI without an API key or DB.

Design choices:
- **Recall over exact-match** on set-valued fields (categories, key terms):
  the model may legitimately add items; we penalise *misses*, not extras.
- **Truncation is an automatic fail** for JSON kinds — a max_tokens cutoff
  produced unparseable/partial output in production (see ai.py KIND_MAX_TOKENS
  note), so we assert stop_reason != "max_tokens".
"""

from __future__ import annotations

from typing import Any, Callable


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _recall(expected: list[str], got: list[str]) -> tuple[float, list[str]]:
    """Fraction of expected items present in got (case-insensitive substring).
    Returns (recall, missing[])."""
    if not expected:
        return 1.0, []
    got_norm = [_norm(g) for g in got]
    missing = []
    hit = 0
    for e in expected:
        en = _norm(e)
        if any(en in g or g in en for g in got_norm):
            hit += 1
        else:
            missing.append(e)
    return hit / len(expected), missing


def score_document_summary(output: dict, expect: dict, meta: dict) -> tuple[float, dict]:
    """document_summary: {doc_type, discipline?, summary, key_terms[]}.
    Scored on: correct doc_type (0.4), key-term recall (0.4), non-empty
    summary within length budget (0.2)."""
    detail: dict[str, Any] = {}
    parts: list[float] = []

    # doc_type classification
    want_type = _norm(expect.get("doc_type"))
    if want_type:
        got_type = _norm(output.get("doc_type"))
        ok = got_type == want_type or want_type in got_type or got_type in want_type
        detail["doc_type"] = {"want": want_type, "got": got_type, "ok": ok}
        parts.append(0.4 if ok else 0.0)

    # key-term recall
    want_terms = expect.get("key_terms") or []
    if want_terms:
        got_terms = output.get("key_terms") or []
        rec, missing = _recall(want_terms, got_terms)
        detail["key_terms"] = {"recall": round(rec, 3), "missing": missing}
        parts.append(0.4 * rec)

    # summary sanity: non-empty and not absurdly long (document_summary budget is short)
    summary = str(output.get("summary") or "")
    ok_summary = 0 < len(summary) <= 1200
    detail["summary"] = {"len": len(summary), "ok": ok_summary}
    parts.append(0.2 if ok_summary else 0.0)

    return (sum(parts) if parts else 0.0), detail


def score_scope_analysis(output: dict, expect: dict, meta: dict) -> tuple[float, dict]:
    """scope_analysis: {categories:[{name, items:[...]}], ...}.
    Scored on: not truncated (hard gate), expected-category recall (0.7),
    every category has ≥1 item (0.3)."""
    detail: dict[str, Any] = {}

    if meta.get("stop_reason") == "max_tokens":
        detail["truncated"] = True
        return 0.0, detail

    cats = output.get("categories") or []
    got_names = [c.get("name", "") for c in cats if isinstance(c, dict)]

    want_cats = expect.get("categories") or []
    rec, missing = _recall(want_cats, got_names)
    detail["categories"] = {"recall": round(rec, 3), "missing": missing, "got": got_names}

    non_empty = [c for c in cats if isinstance(c, dict) and (c.get("items") or [])]
    items_ok = (len(non_empty) / len(cats)) if cats else 0.0
    detail["items_present"] = round(items_ok, 3)

    return (0.7 * rec + 0.3 * items_ok), detail


def score_rag(output: dict, expect: dict, meta: dict) -> tuple[float, dict]:
    """rag: {retrieved: [doc_id...], answer: str}.
    Scored on retrieval recall (0.6 — did we surface the doc(s) that hold the
    answer?) + answer grounding (0.4 — does the answer contain the expected
    facts?). Retrieval quality is the load-bearing half of RAG, hence weighted."""
    detail: dict[str, Any] = {}

    want_docs = expect.get("must_retrieve") or []
    got_docs = output.get("retrieved") or []
    rec, missing = _recall(want_docs, got_docs)
    detail["retrieval"] = {"recall": round(rec, 3), "missing": missing}

    want_facts = expect.get("answer_must_include") or []
    answer = str(output.get("answer") or "")
    fact_rec, fact_missing = _recall(want_facts, [answer]) if want_facts else (1.0, [])
    detail["answer"] = {"recall": round(fact_rec, 3), "missing": fact_missing}

    return (0.6 * rec + 0.4 * fact_rec), detail


# kind → scorer
SCORERS: dict[str, Callable[[dict, dict, dict], tuple[float, dict]]] = {
    "document_summary": score_document_summary,
    "scope_analysis": score_scope_analysis,
    "rag": score_rag,
}
