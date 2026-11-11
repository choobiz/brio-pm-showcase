"""The prompt for editing a record from a written instruction.

Two rules do the safety work here, and both are in the system prompt because
that is where the model is least likely to talk itself out of them:

  1. Return a CHANGE SET, never the document. A rewrite can drop a section
     silently; a list of named changes cannot.
  2. Every change carries `before` — the value the edit expects to replace.
     The caller checks it against the real document and refuses the change if
     it does not match, so an invented path fails loudly instead of writing
     over something nobody looked at.

The model is also told to REFUSE rather than guess. An instruction like "make
it cheaper" has no single correct edit, and silently picking one is worse than
saying so.
"""

from __future__ import annotations

from typing import Any

from app.services.edit_plan import describe

_SYSTEM = """You edit structured construction documents for BRIO, a BC residential
and light-commercial builder. You are given the CURRENT document as a list of
field paths and values, and one instruction from the estimator.

You do NOT return the document. You return a list of individual changes.

Reply with ONLY this JSON object, no markdown and no commentary:

{
  "summary": "one sentence describing what this does",
  "changes": [
    {
      "path": "exact.path[0].from.the.listing",
      "action": "replace" | "remove" | "add",
      "before": <the CURRENT value at that path, copied exactly>,
      "after": <the new value>,
      "why": "one short clause"
    }
  ],
  "refused": ["anything asked for that you did not do, and why"]
}

RULES

- `path` must be copied from the field listing. Never invent one. If the field
  the instruction refers to is not in the listing, refuse instead.
- `before` is required for "replace" and "remove", and must be the value
  currently at that path, copied exactly. It is checked. A change whose
  `before` does not match the document is thrown away.
- "add" appends to a list. Its path ends in `[]`, e.g. `exclusions[]`, and it
  takes `after` only.
- "remove" takes `before` only. Index removals are renumbered by the caller,
  so give each one its CURRENT index from the listing.
- Change only what the instruction asks for. Do not tidy, reword, reorder or
  improve anything else. An unrequested change is a defect here, not a bonus.
- Refuse, in `refused`, anything the instruction does not determine: a target
  the listing does not contain, a figure you would have to invent, or a request
  with more than one reasonable reading. Guessing is worse than stopping.
- Never change a total, subtotal or tax field. Those are recomputed from the
  line items after your changes are applied; editing one directly would put two
  different numbers on the same document.
- If a change alters the WORK but you have no basis to price it, make the change
  and add a line to `refused` saying the price impact is unpriced and needs a
  human. Going from two coats of paint to three is more work; leaving the money
  untouched without saying so hands someone a document that under-prices the
  scope it now describes.
- Keep the client's register: these documents go to the person paying. No
  internal pricing structure (contingency, overhead, profit, margin, markup)
  in any text you write.
"""

_TOTAL_FIELDS = (
    "subtotal", "gst", "total", "cost_impact", "grand_total", "tax",
    "overhead", "profit", "contingency",
)


def build_ai_edit_messages(
    payload: dict[str, Any],
) -> tuple[str, list[dict[str, Any]], int]:
    """(system, messages, max_tokens) for an `ai_edit` job."""
    record_type = payload.get("record_type") or "document"
    label = payload.get("record_label") or ""
    document = payload.get("document") or {}
    instruction = (payload.get("instruction") or "").strip()
    meta = payload.get("project_metadata") or {}

    header = [f"Record: {record_type}" + (f" — {label}" if label else "")]
    if meta.get("name"):
        header.append(f"Project: {meta['name']}")
    if meta.get("address"):
        header.append(f"Address: {meta['address']}")

    text = (
        "\n".join(header)
        + "\n\nCURRENT DOCUMENT — every editable field, with its exact path:\n"
        + describe(document)
        + "\n\nINSTRUCTION FROM THE ESTIMATOR:\n"
        + (instruction or "(none given — refuse everything and say so)")
        + "\n\nReply with ONLY the JSON object described in the system prompt."
    )
    return _SYSTEM, [{"role": "user", "content": [{"type": "text", "text": text}]}], 8000


def strip_total_edits(changes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop any change that touches a derived money field.

    The system prompt forbids it; this enforces it. A total is recomputed from
    the line items when the change set is applied, so a model-authored total
    would either be overwritten — making the edit a lie — or, worse, survive
    and disagree with the lines beneath it.
    """
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for c in changes:
        leaf = str(c.get("path") or "").rsplit(".", 1)[-1].split("[")[0].lower()
        if leaf in _TOTAL_FIELDS:
            dropped.append(
                f"{c.get('path')}: totals are recomputed from the line items, "
                "not edited directly"
            )
            continue
        kept.append(c)
    return kept, dropped
