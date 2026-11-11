"""What the finish is, asked and confirmed — and what the estimate is told.

Amir's rule (2026-10-13). A finish tier ("mid") applied a pile of defaults
nobody chose. Instead:

* **Delivered as** comes first. A SHELL has no finish: drywall, painting,
  flooring/tile and millwork are not included (insulation and doors are). A
  PART SHELL carries a note of which areas are left unfinished. Only a finished
  (or part-finished) space is asked about finishes.
* **Each finish is a question.** The app may propose an answer, read from the
  scope; a person confirms it or writes their own. Only CONFIRMED answers reach
  the estimate as fact. Anything unconfirmed is stated on the estimate as an
  assumption, never silently defaulted.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: The finishes asked about, in the order a walkthrough goes. First draft,
#: Amir to add to or cut.
QUESTIONS: list[dict[str, str]] = [
    {"key": "flooring", "label": "Flooring",
     "question": "What flooring goes where? (living areas, bedrooms, wet areas)"},
    {"key": "cabinets", "label": "Cabinets",
     "question": "What cabinets: stock, semi-custom or custom, and what door style?"},
    {"key": "counters", "label": "Counters",
     "question": "What countertops: laminate, quartz, stone?"},
    {"key": "plumbing_fixtures", "label": "Plumbing fixtures",
     "question": "What range of plumbing fixtures, and are any owner-supplied?"},
    {"key": "interior_doors_trim", "label": "Interior doors and trim",
     "question": "What interior doors and trim: hollow or solid core, MDF or wood casing?"},
    {"key": "paint", "label": "Paint",
     "question": "How many coats and colours, and are ceilings painted?"},
    {"key": "electrical_fixtures", "label": "Light fixtures",
     "question": "What lighting: pot lights where, and an allowance or chosen fixtures elsewhere?"},
]
KEYS = [q["key"] for q in QUESTIONS]

#: What a shell leaves out, in the words the estimate and the client see.
SHELL_EXCLUDED = [
    "Drywall (insulation is included)",
    "Painting",
    "Flooring and tile",
    "Millwork and casework",
]

#: Residential divisions a shell leaves out WHOLE. 06 "Insulation, Drywall &
#: Acoustic" is not here: insulation stays in, so only the prompt can remove
#: the drywall half of it.
SHELL_ZERO_DIVISIONS_RESIDENTIAL = ("07", "08", "09")


def _row(db: Session, project_id: UUID) -> dict[str, Any] | None:
    from sqlalchemy import text as _T

    r = db.execute(_T(
        "SELECT delivered_as, part_shell_note, finish_selections FROM projects WHERE id = :p"),
        {"p": str(project_id)}).mappings().first()
    if r is None:
        return None
    sel = r["finish_selections"]
    if isinstance(sel, str):
        sel = json.loads(sel or "{}")
    return {"delivered_as": r["delivered_as"], "part_shell_note": r["part_shell_note"],
            "selections": sel or {}}


def finish_state(db: Session, project_id: UUID) -> dict[str, Any] | None:
    """What the Estimate page shows: delivered-as, the note, and each question
    with its answer, suggestion and whether a person confirmed it."""
    r = _row(db, project_id)
    if r is None:
        return None
    asks = r["delivered_as"] in ("finished", "part_shell")
    qs = []
    for q in QUESTIONS:
        s = r["selections"].get(q["key"]) or {}
        qs.append({**q, "answer": s.get("answer"), "suggested": s.get("suggested"),
                   "why": s.get("why"), "confirmed": bool(s.get("confirmed")),
                   "confirmed_at": s.get("confirmed_at")})
    return {
        "delivered_as": r["delivered_as"],
        "part_shell_note": r["part_shell_note"],
        "asks_finishes": asks,
        "questions": qs if asks else [],
        "confirmed": sum(1 for q in qs if q["confirmed"]) if asks else 0,
        "total": len(qs) if asks else 0,
        "shell_excluded": SHELL_EXCLUDED if r["delivered_as"] == "shell" else [],
    }


def set_answer(db: Session, project_id: UUID, key: str, answer: str | None,
               confirmed: bool, user_id: str | None) -> None:
    """Write one answer. Confirming records who and when; changing an answer
    without confirming leaves it unconfirmed."""
    from app.models.project import Project

    if key not in KEYS:
        raise KeyError(key)
    p = db.get(Project, project_id)
    sel = dict(p.finish_selections or {})
    cur = dict(sel.get(key) or {})
    cur["answer"] = (answer or "").strip() or None
    cur["confirmed"] = bool(confirmed and cur["answer"])
    if cur["confirmed"]:
        cur["confirmed_at"] = datetime.now(timezone.utc).isoformat()
        cur["confirmed_by"] = user_id
    else:
        cur.pop("confirmed_at", None)
        cur.pop("confirmed_by", None)
    sel[key] = cur
    p.finish_selections = sel
    db.flush()


# ── suggestions ───────────────────────────────────────────────────────────

_SUGGEST_SYSTEM = (
    "You help a residential builder in Vancouver, BC settle a project's finishes "
    "before it is priced. For each finish asked about, propose a short, specific "
    "answer (a product type or range, not a brand list) and say in a few words "
    "why: quote what the scope says when it says something, and say plainly when "
    "it says nothing and you are suggesting a common choice for this kind of "
    "project. Never invent a decision the scope does not support. If the scope "
    "EXCLUDES an item, set \"suggested\" to null and say so in \"why\". Reply with "
    'JSON only: {"<key>": {"suggested": "..." or null, "why": "..."}, ...}'
)


def suggest(db: Session, project_id: UUID, *, call=None) -> dict[str, dict[str, str]]:
    """Propose answers for every UNCONFIRMED question, from the latest scope.

    Stored as suggestions; nothing is confirmed. An answer a person already
    typed is left alone — only empty answers are filled with the suggestion.
    `call` is the Anthropic call, injectable for tests.
    """
    from sqlalchemy import text as _T

    from app.models.project import Project

    if call is None:
        from app.services.ai import MODEL_SONNET, call_anthropic

        def call(system, messages):
            return call_anthropic(MODEL_SONNET, system, messages, max_tokens=1500)

    p = db.get(Project, project_id)
    sel = dict(p.finish_selections or {})
    todo = [q for q in QUESTIONS if not (sel.get(q["key"]) or {}).get("confirmed")]
    if not todo:
        return {}
    scope = db.execute(_T(
        "SELECT payload FROM scopes WHERE project_id = :p ORDER BY version DESC LIMIT 1"),
        {"p": str(project_id)}).scalar()
    scope_text = json.dumps(scope, default=str)[:20000] if scope else "(no scope written yet)"
    msg = (
        f"Project: {p.name} · type {p.type or 'not set'} · delivered as {p.delivered_as}"
        + (f" · part shell: {p.part_shell_note}" if p.part_shell_note else "")
        + f"\n\nScope of work (JSON):\n{scope_text}\n\nFinishes to propose:\n"
        + "\n".join(f'- "{q["key"]}": {q["question"]}' for q in todo)
    )
    result = call(_SUGGEST_SYSTEM, [{"role": "user", "content": msg}])
    text = (getattr(result, "text", None) or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        got = json.loads(text)
    except ValueError:
        logger.warning("finish_spec.suggest: unparseable reply for %s", project_id)
        raise ValueError("The suggestion came back unreadable; try again.") from None
    out: dict[str, dict[str, str]] = {}
    for q in todo:
        g = got.get(q["key"]) if isinstance(got, dict) else None
        if not isinstance(g, dict):
            continue
        cur = dict(sel.get(q["key"]) or {})
        # An answer equal to the previous suggestion was never typed by a person,
        # so a new suggestion may replace it. Anything else a person typed stays.
        typed = cur.get("answer") and cur.get("answer") != cur.get("suggested")
        sug = g.get("suggested")
        sug = str(sug).strip()[:300] if isinstance(sug, str) and sug.strip() else None
        cur["suggested"] = sug
        cur["why"] = str(g.get("why") or "")[:300]
        if not typed:
            cur["answer"] = sug
        cur["confirmed"] = False
        sel[q["key"]] = cur
        out[q["key"]] = {"suggested": cur["suggested"], "why": cur["why"]}
    p.finish_selections = sel
    # Flushed so a raw-SQL read in the same transaction (finish_state) sees it.
    db.flush()
    return out


# ── what the estimate is told ─────────────────────────────────────────────


def spec_for_estimate(db: Session, project_id: UUID) -> dict[str, Any] | None:
    """The block the estimate prompt reads. None when nothing is known."""
    r = _row(db, project_id)
    if r is None:
        return None
    d = r["delivered_as"]
    spec: dict[str, Any] = {"delivered_as": d}
    if d == "shell":
        spec["excluded"] = SHELL_EXCLUDED
        return spec
    if d == "part_shell":
        spec["part_shell_note"] = r["part_shell_note"]
    confirmed, unconfirmed = [], []
    for q in QUESTIONS:
        s = r["selections"].get(q["key"]) or {}
        if s.get("confirmed") and s.get("answer"):
            confirmed.append({"label": q["label"], "answer": s["answer"]})
        else:
            unconfirmed.append(q["label"])
    spec["confirmed"] = confirmed
    spec["unconfirmed"] = unconfirmed
    return spec


def prompt_block(spec: dict[str, Any] | None) -> str | None:
    """The FINISH section of the estimate prompt."""
    if not spec:
        return None
    d = spec.get("delivered_as")
    if d == "shell":
        return (
            "DELIVERED AS A SHELL — confirmed by BRIO.\n"
            "Do NOT price: drywall, painting, flooring and tile, millwork and casework. "
            "Insulation IS priced (keep it in its division without the drywall), and doors "
            "are priced. Add each excluded item to `exclusions` in these words: "
            + "; ".join(SHELL_EXCLUDED) + "."
        )
    lines = []
    if d == "part_shell":
        note = (spec.get("part_shell_note") or "").strip()
        lines.append(
            "PART SHELL — some areas are left unfinished"
            + (f": {note}" if note else " (which areas is not recorded yet; state your assumption)")
            + ". In those areas only, do not price drywall, painting, flooring/tile or millwork, "
              "and list them under `exclusions`."
        )
    elif d == "finished":
        lines.append("DELIVERED FINISHED.")
    else:
        lines.append("Whether the space is delivered finished or as a shell is NOT confirmed; "
                     "price it finished and say so in `assumptions`.")
    confirmed = spec.get("confirmed") or []
    if confirmed:
        lines.append("CONFIRMED FINISHES — chosen by BRIO; price exactly these:")
        lines += [f"- {c['label']}: {c['answer']}" for c in confirmed]
    unconfirmed = spec.get("unconfirmed") or []
    if unconfirmed:
        lines.append(
            "NOT YET CONFIRMED: " + ", ".join(unconfirmed) + ". For each, price a typical "
            "choice for this kind of project AND state the choice you assumed in `assumptions` "
            "(e.g. \"Flooring assumed: LVP throughout; not confirmed\")."
        )
    return "\n".join(lines)


def apply_shell_exclusions(payload: dict[str, Any], spec: dict[str, Any] | None,
                           set_code: str | None) -> bool:
    """The part of a shell that can be enforced in code, after the model.

    On the residential set, divisions 07 painting, 08 flooring/tile and 09
    millwork are zeroed whatever the model priced, and the exclusions are
    listed. Drywall sits in 06 with insulation, so it is left to the prompt.
    Returns True when anything changed.
    """
    if not spec or spec.get("delivered_as") != "shell":
        return False
    changed = False
    if set_code and "resid" in set_code.lower():
        for cat in payload.get("categories") or []:
            name = (cat.get("name") or "").strip()
            if name[:2] in SHELL_ZERO_DIVISIONS_RESIDENTIAL and (cat.get("total") or cat.get("items")):
                cat.update(items=[], subtotalRaw=0, contingencyAmount=0, total=0,
                           description="Not included: delivered as a shell.")
                changed = True
    ex = list(payload.get("exclusions") or [])
    for e in SHELL_EXCLUDED:
        if not any(e.split(" (")[0].lower() in str(x).lower() for x in ex):
            ex.append(e)
            changed = True
    payload["exclusions"] = ex
    return changed


def apply_for_project(db: Session, project_id: UUID, payload: dict[str, Any]) -> bool:
    """`apply_shell_exclusions` with the project's own spec and division set."""
    from sqlalchemy import text as _T

    from app.services.divisions import division_context

    spec = spec_for_estimate(db, project_id)
    if not spec or spec.get("delivered_as") != "shell":
        return False
    ptype = db.execute(_T("SELECT type FROM projects WHERE id = :p"), {"p": str(project_id)}).scalar()
    ctx = division_context(db, ptype) or {}
    return apply_shell_exclusions(payload, spec, ctx.get("set_code"))
