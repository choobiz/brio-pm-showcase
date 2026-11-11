"""Approximate cost of an assistant request, shown BEFORE it runs and approved
by the person asking (Amir, 2026-10-13). The approved amount is then a hard
limit: the server stops a request that would spend more than was approved.

Estimates are "up to" figures: the median of recent real costs for the same
kind of step, with headroom, or measured defaults until there is history.
Defaults from the 1234 runs on 2026-10-13: an Ask question $0.02-0.04; a plan
$0.06-0.08; a three-document draft about $0.48 on Sonnet; reading one large
drawing sheet $0.13-0.38 on Opus.

Work done on the Mac (Claude Code, Amir's subscription) costs nothing here;
the estimate says so, and the approval only covers the server's fallback.
"""
from __future__ import annotations

from decimal import ROUND_UP, Decimal
from statistics import median
from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

DEFAULTS = {"ask": Decimal("0.04"), "plan": Decimal("0.10"), "draft": Decimal("0.60"), "revise": Decimal("0.45")}
#: One drawing page not read before (tiled Opus read).
PER_UNREAD_DRAWING = Decimal("0.40")
HEADROOM = Decimal("1.5")
MIN_SAMPLES = 3


def _up(x: Decimal) -> Decimal:
    """Round up to the next cent (to the next 5 cents above a dollar)."""
    step = Decimal("0.05") if x >= 1 else Decimal("0.01")
    return (x / step).quantize(Decimal("1"), rounding=ROUND_UP) * step


def _history(db: Session, purpose: str, stage: str | None) -> list[Decimal]:
    if purpose == "ask":
        rows = db.execute(_T("SELECT cost_usd FROM ai_spend WHERE purpose = 'ask' "
                             "ORDER BY created_at DESC LIMIT 30")).scalars().all()
        return [Decimal(r) for r in rows]
    # One figure per request-stage: the sum of its calls, excluding drawing reads
    # (those are estimated separately, per unread page).
    rows = db.execute(_T("""
        SELECT sum(cost_usd) FROM ai_spend
         WHERE purpose = 'prep' AND stage = :s AND model <> 'claude-opus-5-5'
      GROUP BY prep_request_id ORDER BY max(created_at) DESC LIMIT 20"""), {"s": stage}).scalars().all()
    return [Decimal(r) for r in rows]


def _base(db: Session, purpose: str, stage: str | None) -> tuple[Decimal, str]:
    h = _history(db, purpose, stage)
    if len(h) >= MIN_SAMPLES:
        return Decimal(median(h)) * HEADROOM, f"median of the last {len(h)} x {HEADROOM}"
    return DEFAULTS[stage or purpose], "measured default (not enough history yet)"


#: AI jobs (scope, estimate, describe-a-change) until a model has history. From
#: prod medians on 2026-10-15, re-priced for the 5.5 models: a scope ran $0.32 on
#: Opus 4.7 (9k in, 2.3k out), an edit $0.06; the full estimate recorded no cost
#: at all until that day, so its default is 7 Sonnet calls of up to 16k out.
JOB_DEFAULTS = {"scope_analysis": Decimal("0.50"), "ai_edit": Decimal("0.15"),
                "estimate_full": Decimal("2.00"), "estimate_generation": Decimal("0.50")}


def ai_job(db: Session, kind: str) -> dict[str, Any]:
    """Up-to cost of one AI job: the median of recent finished jobs of the same
    kind ON THE MODEL IT NOW USES, with headroom. History from an older, dearer
    model would overstate it; a cheaper one would understate it."""
    from app.services.ai import model_for_kind

    model = model_for_kind("estimate_generation" if kind == "estimate_full" else kind)
    rows = db.execute(_T("""
        SELECT cost_usd FROM ai_jobs
         WHERE kind = :k AND status = 'done' AND model = :m AND cost_usd > 0
      ORDER BY created_at DESC LIMIT 20"""), {"k": kind, "m": model}).scalars().all()
    if len(rows) >= MIN_SAMPLES:
        usd, how = Decimal(median([Decimal(r) for r in rows])) * HEADROOM, \
            f"median of the last {len(rows)} x {HEADROOM}"
    else:
        usd, how = JOB_DEFAULTS.get(kind, Decimal("0.50")), "measured default (not enough history yet)"
    return {"kind": kind, "usd": float(_up(usd)), "how": how}


def ask(db: Session) -> dict[str, Any]:
    usd, how = _base(db, "ask", None)
    return {"usd": float(_up(usd)), "how": how}


def unread_drawings(db: Session, project_id: UUID, document_ids: list[str]) -> list[str]:
    """Picked documents that will need a visual read: drawings, surveys, photos
    and scans with no saved reading yet."""
    if not document_ids:
        return []
    rows = db.execute(_T("""
        SELECT d.filename FROM documents d
         WHERE d.project_id = :p AND d.id = ANY(:ids)
           AND (d.category IN ('drawing', 'survey', 'photo')
                OR NOT EXISTS (SELECT 1 FROM document_text_pages t WHERE t.document_id = d.id))
           AND NOT EXISTS (SELECT 1 FROM drawing_facts f WHERE f.document_id = d.id)"""),
                      {"p": str(project_id), "ids": [str(i) for i in document_ids]}).scalars().all()
    return list(rows)


def prep(db: Session, project_id: UUID, stage: str, document_ids: list[str]) -> dict[str, Any]:
    from app.services.prep import mac_available

    base, how = _base(db, "prep", stage)
    unread = unread_drawings(db, project_id, document_ids)
    # Drawings are read once per project; only the first step that needs them pays.
    reads = PER_UNREAD_DRAWING * len(unread) if stage in ("plan", "draft") else Decimal(0)
    total = _up(base + reads)
    lines = [{"label": f"{stage.capitalize()} step", "usd": float(_up(base)), "how": how}]
    if unread:
        lines.append({"label": f"Reading {len(unread)} drawing(s) for the first time", "usd": float(reads),
                      "how": ", ".join(unread[:5])})
    return {"stage": stage, "server_usd": float(total), "mac_available": mac_available(db), "lines": lines}
