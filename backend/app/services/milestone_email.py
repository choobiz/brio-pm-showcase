"""Drafting a milestone email (an `email_draft` AI job).

The assistant writes the booking or request from what the project holds. It
never sends: the draft lands in milestone_emails for the PM to read, edit and
confirm (Amir, 2026-10-16: "There should always be a confirmation before the
emails go out so the PM can review the email").
"""
from __future__ import annotations

import json
from typing import Any

MAX_TOKENS = 1500

SYSTEM = """You draft short, professional emails for a residential general contractor's
project manager (BRIO, North Vancouver, BC) to engineers, land surveyors, the
municipality and trades: booking a field review, survey or inspection, asking for a
letter or memo, sending a submittal for review, or giving a trade notice.

Write as the PM, in plain words, the way a busy builder writes to a professional they
work with: greeting by first name when known, the ask in the first two sentences,
the dates, what you need back in writing, and a one-line close with the sender's name
and phone. No marketing tone, no filler, no "I hope this finds you well".

Call the authority having jurisdiction by the name in CONTEXT.municipality (in North
Vancouver District it is "the District", never "the City").

Use ONLY facts in the CONTEXT. Where something the email needs is missing or
uncertain (a date nobody confirmed, a recipient's name, whether a second visit is
needed), write it as a bracketed placeholder like [confirm date] rather than inventing
it. Quote the professional's own requirement when the context has it (e.g. their
drawing note on notice periods). Mention the documents the municipality will ask for
when the reply should include them (a written field memo, a certificate). Mention
attachments only if the context lists them as attached.

Respond with JSON only: {"subject": "...", "body": "..."}. The subject is short and
starts with the site address. Do not add any reference tag to the subject; the system
adds it."""

KIND_ASK = {
    "booking": "Book this visit or inspection.",
    "document_request": "Ask for this letter, memo or certificate.",
    "submittal": "Send this for review before it goes ahead.",
    "notice": "Give this trade or supplier notice of the date and what is needed.",
}


def build_email_draft_messages(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], int]:
    ctx = payload.get("context") or {}
    item = ctx.get("item") or {}
    ask = KIND_ASK.get(item.get("email_kind") or "booking", KIND_ASK["booking"])
    attached = payload.get("attachments") or []
    extra = (payload.get("instructions") or "").strip()
    user = [
        f"TASK: {ask}",
        "CONTEXT (JSON):",
        json.dumps(ctx, default=str, indent=1)[:24000],
        "ATTACHED TO THIS EMAIL: " + (", ".join(attached) if attached else "nothing"),
    ]
    if extra:
        user.append(f"THE PM ADDS: {extra}")
    return SYSTEM, [{"role": "user", "content": "\n".join(user)}], MAX_TOKENS


def parse_email_draft(text: str) -> dict[str, str]:
    """{"subject","body"} from the model's reply; tolerant of a fenced block."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = s.find("{"), s.rfind("}")
    data = json.loads(s[start:end + 1])
    subject, body = str(data.get("subject") or "").strip(), str(data.get("body") or "").strip()
    if not subject or not body:
        raise ValueError("the draft came back without a subject or body")
    return {"subject": subject, "body": body}
