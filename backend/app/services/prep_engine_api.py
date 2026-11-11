"""The API drafting engine: the fallback when the Mac is not available.

Sonnet 5.5 runs the stage (cheaper); a drawing page is read by Opus 5.5 from
tiles, once, and kept as a drawing fact for every later request. Every call is
recorded in `ai_spend`; the stage refuses to start below `MIN_TO_START` of
budget left, and stops if the month's prep spend passes the cap mid-run.
"""
from __future__ import annotations

import base64
import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.prep import DrawingFact, PrepRequest
from app.services import prep as P
from app.services import prep_spend as S
from app.services import prep_tools as T
from app.services.prep_prompt import instructions

log = logging.getLogger(__name__)

DRAFT_MODEL = "claude-sonnet-5-5"
READ_MODEL = "claude-opus-5-5"
MAX_TURNS = 20
MIN_TO_START = Decimal("0.30")
#: Output budgets include the model's thinking. 2026-10-13: 8k for a drawing
#: read and 16k for a whole draft both ran out on 1234 (the read came back
#: empty, twice; the draft's JSON was cut off and failed validation).
READ_MAX_TOKENS = 20_000
DRAFT_MAX_TOKENS = 32_000
#: Set explicitly: without it the SDK refuses non-streaming calls this large.
CALL_TIMEOUT = 900

READ_PROMPT = """These are overlapping tiles of ONE drawing page, left to right, top to bottom. Write down everything legible that a builder would need: every dimension, size, spacing, material, specification, note, detail title (say whether anything is drawn under it), and schedule, each with where on the sheet it is. Write "not legible" rather than guess. Markdown, grouped by detail or area."""

TOOLS = [
    {"name": "read_text", "description": "Text of a project document, optionally only some pages.",
     "input_schema": {"type": "object", "properties": {
         "document_id": {"type": "string"}, "pages": {"type": "array", "items": {"type": "integer"}}},
         "required": ["document_id"]}},
    {"name": "search", "description": "Search the text of every document in this project.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "read_drawing", "description": ("Read one page of a drawing, scan or photo visually (tiled), and "
                                             "return everything legible on it. Cached per page."),
     "input_schema": {"type": "object", "properties": {
         "document_id": {"type": "string"}, "page_no": {"type": "integer"}}, "required": ["document_id"]}},
    {"name": "submit", "description": "PLAN stage only: submit the plan JSON the instructions describe.",
     "input_schema": {"type": "object", "properties": {"result": {"type": "object"}}, "required": ["result"]}},
    {"name": "submit_document", "description": ("DRAFT and REVISE stages: submit ONE finished document (one item "
                                                "of the instructions' `documents` list). Call once per document."),
     "input_schema": {"type": "object", "properties": {"document": {"type": "object"}}, "required": ["document"]}},
    {"name": "finish", "description": ("DRAFT and REVISE stages: after every document is submitted, finish with "
                                       "the conflicts, assumptions and drawing facts."),
     "input_schema": {"type": "object", "properties": {
         "conflicts": {"type": "array", "items": {"type": "string"}},
         "assumptions": {"type": "array", "items": {"type": "string"}},
         "drawing_facts": {"type": "array", "items": {"type": "object"}}}}},
]

#: Draft results are assembled from one call per document: a single JSON for
#: three documents plus thinking overran the output budget on 1234.
HOW_TO_SUBMIT = {
    "plan": "Finish by calling submit with the plan JSON.",
    "draft": "Submit each document with its own submit_document call, then call finish.",
    "revise": "Submit each revised document with its own submit_document call (all of them, changed or not), then call finish.",
}


class BudgetExceeded(Exception):
    pass


class ApprovalExceeded(Exception):
    """The request reached what the person approved (Amir 2026-10-13)."""


def _charge(db: Session, req: PrepRequest, model: str, usage: Any) -> None:
    c = S.record(db, "prep", model, usage, prep_request_id=req.id, stage=req.stage)
    req.cost_usd = (req.cost_usd or 0) + c
    db.commit()
    if S.month_spend(db, "prep") > S.cap(db):
        raise BudgetExceeded()
    if req.cost_usd > (req.approved_usd or 0):
        raise ApprovalExceeded()


def _read_drawing(db: Session, req: PrepRequest, client: Any, document_id: str, page_no: int) -> str:
    from uuid import UUID

    T._doc(db, req.project_id, document_id)
    have = db.query(DrawingFact).filter(DrawingFact.document_id == UUID(document_id),
                                        DrawingFact.page_no == page_no).first()
    if have:
        return have.facts_md
    tiles = T.tiles(db, req.project_id, document_id, page_no)
    P._progress(req, f"Reading a drawing page ({len(tiles)} tiles)")
    db.commit()
    content: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                     "data": base64.b64encode(t).decode()}} for t in tiles]
    content.append({"type": "text", "text": READ_PROMPT})
    r = client.messages.create(model=READ_MODEL, max_tokens=READ_MAX_TOKENS, timeout=CALL_TIMEOUT,
                               messages=[{"role": "user", "content": content}])
    _charge(db, req, READ_MODEL, r.usage)
    text = "\n".join(getattr(b, "text", "") or "" for b in r.content if getattr(b, "type", "") == "text").strip()
    if getattr(r, "stop_reason", None) == "max_tokens":
        # A cut-off read is never cached: the next request would trust it.
        return (text + "\n\n[READ CUT OFF: incomplete; do not rely on anything not listed above]") if text \
            else "The page read ran out of room and returned nothing. Read its text instead, or ask."
    if text:
        P.save_fact(db, {"document_id": document_id, "page_no": page_no, "facts": text},
                    engine="api", model=READ_MODEL)
        db.commit()
    return text or "Nothing legible was returned for this page."


def _tool(db: Session, req: PrepRequest, client: Any, name: str, args: dict[str, Any]) -> str:
    try:
        if name == "read_text":
            out: Any = T.document_text(db, req.project_id, args["document_id"], args.get("pages"))
        elif name == "search":
            out = T.search(db, req.project_id, args["query"])
        elif name == "read_drawing":
            return _read_drawing(db, req, client, args["document_id"], int(args.get("page_no") or 1))
        else:
            out = {"error": f"unknown tool {name}"}
    except (BudgetExceeded, ApprovalExceeded):
        raise
    except Exception as e:  # noqa: BLE001 — the model gets the error and can try something else
        detail = getattr(e, "detail", None) or str(e)
        out = {"error": str(detail)[:300]}
    return json.dumps(out, default=str)[:60000]


def run(db: Session, req: PrepRequest, client: Any = None) -> None:
    """Run the request's current stage to a result, a failure, or a budget stop."""
    from app.services.ai import anthropic_client

    c = client or anthropic_client()
    context = P.job_context(db, req)
    system = [{"type": "text", "text": instructions(req.stage), "cache_control": {"type": "ephemeral"}}]
    messages: list[dict[str, Any]] = [{"role": "user", "content": [
        {"type": "text", "text": "Job context (JSON):\n" + json.dumps(context, default=str),
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"Do the {req.stage} stage now. {HOW_TO_SUBMIT.get(req.stage, '')}"}]}]
    docs: list[dict[str, Any]] = []
    try:
        for _ in range(MAX_TURNS):
            r = c.messages.create(model=DRAFT_MODEL, max_tokens=DRAFT_MAX_TOKENS, timeout=CALL_TIMEOUT,
                                  system=system, tools=TOOLS, messages=messages)
            _charge(db, req, DRAFT_MODEL, r.usage)
            content = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b for b in r.content]
            cut = getattr(r, "stop_reason", None) == "max_tokens"
            # A tool call cut off mid-JSON is incomplete: never act on it.
            calls = [] if cut else [b for b in content if b.get("type") == "tool_use"]
            submit = next((b for b in calls if b["name"] == "submit"), None)
            if submit and req.stage == "plan":
                P.apply_result(db, req, (submit.get("input") or {}).get("result") or {}, engine="api")
                db.commit()
                return
            # finish is acted on only after every document in the same reply is
            # taken: the model often sends submit_document and finish together
            # (rollout test 2026-10-14 failed a draft with its document in hand).
            finish = next((b for b in calls if b["name"] == "finish"), None) if req.stage != "plan" else None
            if not calls:
                nudge = ("Your last reply was cut off before it finished. Submit one document per "
                         "submit_document call, keeping each call short." if cut else HOW_TO_SUBMIT.get(req.stage, ""))
                messages += [{"role": "assistant", "content": [b for b in content if b.get("type") != "tool_use"]
                              or [{"type": "text", "text": "(cut off)"}]},
                             {"role": "user", "content": nudge}]
                continue
            messages.append({"role": "assistant", "content": content})
            results = []
            rejected = False
            for b in calls:
                if b["name"] == "submit_document":
                    d = (b.get("input") or {}).get("document") or {}
                    try:
                        P.DraftDoc.model_validate(d)
                        docs.append(d)
                        out = f"Received document {len(docs)}: {d.get('title')}"
                    except Exception as e:  # noqa: BLE001
                        rejected = True
                        out = f"Rejected, wrong shape: {str(e)[:300]}. Fix and submit it again."
                elif b["name"] == "finish" and finish is not None:
                    out = ("Not finished: a document in this reply was rejected. Fix it, submit it again, "
                           "then finish." if rejected else "Finished.")
                elif b["name"] in ("submit", "finish"):
                    out = f"{b['name']} is not used in the {req.stage} stage. {HOW_TO_SUBMIT.get(req.stage, '')}"
                else:
                    out = _tool(db, req, c, b["name"], dict(b.get("input") or {}))
                results.append({"type": "tool_result", "tool_use_id": b["id"], "content": out})
            if finish is not None and not rejected:
                if not docs:
                    P.fail(db, req, "The server engine finished without submitting any document.")
                    db.commit()
                    return
                f = finish.get("input") or {}
                P.apply_result(db, req, {"documents": docs, "conflicts": f.get("conflicts") or [],
                                         "assumptions": f.get("assumptions") or [],
                                         "drawing_facts": f.get("drawing_facts") or []}, engine="api")
                db.commit()
                return
            messages.append({"role": "user", "content": results})
            db.commit()
        P.fail(db, req, "The server engine ran out of steps without a result.")
    except BudgetExceeded:
        P.fail(db, req, f"Stopped: this month's ${S.cap(db)} budget for preparing documents is used up. "
                        "Turn on the Mac, or ask Amir to raise the budget.")
    except ApprovalExceeded:
        P.fail(db, req, f"Stopped at the ${req.approved_usd:.2f} you approved (spent ${req.cost_usd:.2f}). "
                        "Approve more with Retry to finish this step, or turn on the Mac.")
    except Exception as e:  # noqa: BLE001
        log.exception("prep api engine failed for %s", req.id)
        P.fail(db, req, f"The server engine failed: {str(e)[:200]}")
    db.commit()


def run_one(client: Any = None) -> bool:
    """Worker hook: reap stuck stages, then take one stage the Mac did not."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        P.reap(db)
        db.commit()
        req = P.claim(db, "api")
        if req is None:
            db.commit()
            return False
        if (req.approved_usd or 0) - (req.cost_usd or 0) <= 0:
            P.fail(db, req, "The Mac didn't pick this up and no server cost was approved for this step. "
                            "Approve the estimate with Retry, or turn on the Mac.")
            db.commit()
            return True
        if S.remaining(db) < MIN_TO_START:
            P.fail(db, req, f"The Mac wasn't available and this month's ${S.cap(db)} budget for preparing "
                            f"documents is used up (${S.month_spend(db, 'prep'):.2f} spent). Turn on the Mac "
                            "and press Retry, or ask Amir to raise the budget.")
            db.commit()
            return True
        db.commit()
        run(db, req, client)
    return True
