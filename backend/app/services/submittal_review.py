"""Submittal review: the prompt, reading the reply, the status, the order gate
and the question emails.

Wireframes approved by Amir 2026-10-17. Rules baked in from the 1234 EcoBloc
review (16 Oct): every value cites the sheet and the label it was read from;
pipes and structures are joined by their labels, never by an elevation printed
next to them (a wrong tank topology was once read off adjacency); a value the
reviewer cannot find is "Not shown", never a guess.

The questions it writes become the emails as they stand: no second model call,
and the PM edits them before copying to Gmail. Keystone never sends them.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.services.submittal_checklists import Checklist

#: 12000 cut the first live review off mid-question (2026-10-17); Opus spends
#: part of this on its reasoning. The worker doubles it once if still cut off.
MAX_TOKENS = 24000
RESULTS = ("critical", "confirm", "match", "not_shown")
ASK_WHO = ("engineer", "supplier", "none")
#: The PM's call on a finding: ask the question, accept it as is, or (after an
#: answer came back) mark it answered.
CALLS = ("ask", "fine", "answered")
PREVIOUS = ("fixed", "still_open", "new")
OPEN_RESULTS = ("critical", "confirm", "not_shown")

SYSTEM = """You review construction submittals (shop drawings, product data, supplier
quotes) for a residential general contractor in British Columbia, before anything is
ordered or built. You compare the SUBMITTAL files against the GOVERNING files (the
engineer's or architect's documents for this project) one check at a time.

Rules, all of them strict:
1. Every value you report cites where it was read: the file, the sheet or page, and the
   label or note it came from (e.g. "Shop p4 · attenuation detail", "SMP R3 · Plate 2R3,
   Sump #2 label"). No citation, no value.
2. Join pipes, sumps and structures ONLY by their labels ("OVERFLOW FROM ECOBLOCK TANK 2"),
   never by an elevation or a line that happens to sit next to them on the sheet.
3. If a value is not on a document, say "Not shown" for that side. Never infer it, never
   assume a typical value.
4. Report every check in the checklist, in order, even when neither side shows it.
5. result is one of:
   - "critical": a mismatch that would make the work wrong or fail its purpose if built as drawn;
   - "confirm": an ambiguity, a self-conflict in a document, a value missing on one side, or a
     mismatch someone must decide;
   - "match": both sides agree (state both values);
   Keep it tight: each value and "why" in one short sentence (30 words at most), each
   citation short; the question may be longer when it needs the numbers.
   - "not_shown": neither side shows it.
6. For every critical, confirm or not_shown finding write ONE question, as the PM would ask
   it, plain and specific with the numbers, and say who answers it: "engineer" (a design
   decision) or "supplier" (what they will supply, a revised drawing or a re-quote).
7. If a quote is among the submittal files, read it: supplier, number, date, subtotal, tax,
   total, and whether its parts list matches the drawing. List what it assumes that the
   project may not want (e.g. a pipe size). Copy each priced line as written: description,
   SKU or part number, quantity, unit, unit price, amount. Never compute a unit price the
   quote doesn't show; leave it null.
8. When PREVIOUS FINDINGS are given (an earlier revision of this submittal), mark each
   finding "previous": "fixed" (was open, now matches), "still_open" (was open, still open)
   or "new" (not raised before).

Respond with JSON only:
{"summary": "one or two sentences: what it is and the headline",
 "findings": [{"check_key": "...", "check": "...", "shop": "...", "shop_ref": "...",
               "plan": "...", "plan_ref": "...", "result": "critical|confirm|match|not_shown",
               "why": "one sentence", "ask_who": "engineer|supplier|none", "question": "... or empty",
               "previous": "fixed|still_open|new or null"}],
 "quote": {"supplier": "...", "number": "...", "date": "...", "subtotal": 0, "tax": 0,
           "total": 0, "bom_matches": true, "notes": ["..."],
           "lines": [{"description": "...", "sku": "...", "qty": 0, "unit": "...",
                      "unit_price": 0, "amount": 0}]} or {}}"""


def build_submittal_review_messages(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], int]:
    checklist = payload.get("checklist") or {}
    lines = [
        f"SUBMITTAL: {payload.get('name')} · {payload.get('revision')} · from {payload.get('supplier') or 'unknown'}",
        f"TRADE CHECKLIST: {checklist.get('label')}",
    ]
    for i, c in enumerate(checklist.get("checks") or [], 1):
        lines.append(f"{i}. [{c['key']}] {c['label']}: {c['what']}")
    if payload.get("previous_findings"):
        lines.append("PREVIOUS FINDINGS (the earlier revision, with the PM's call on each):")
        lines.append(json.dumps(payload["previous_findings"], indent=1)[:12000])
    if (payload.get("instructions") or "").strip():
        lines.append(f"THE PM ADDS: {payload['instructions'].strip()}")
    content: list[dict[str, Any]] = [{"type": "text", "text": "\n".join(lines)}]
    for side in ("submittal", "governing"):
        for d in payload.get(f"{side}_documents") or []:
            content.append({"type": "text", "text": f"{side.upper()} FILE: {d.get('caption')}"})
            if d.get("media_type", "").startswith("image/"):
                content.append({"type": "image", "source": {"type": "base64", "media_type": d["media_type"],
                                                            "data": d["data"]}})
            else:
                content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                               "data": d["data"]}})
        for t in payload.get(f"{side}_texts") or []:
            content.append({"type": "text", "text": f"{side.upper()} FILE: {t.get('caption')}\n{t.get('text') or ''}"})
    return SYSTEM, [{"role": "user", "content": content}], MAX_TOKENS


def _json(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    return json.loads(s[start:end + 1])


def _money(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return round(float(re.sub(r"[^\d.\-]", "", str(v))), 2)
    except ValueError:
        return None


def _lines(raw: Any) -> list[dict[str, Any]]:
    out = []
    for i, ln in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(ln, dict) or not str(ln.get("description") or "").strip():
            continue
        out.append({"i": i, "description": str(ln["description"]).strip(), "sku": (str(ln.get("sku") or "").strip() or None),
                    "qty": _money(ln.get("qty")), "unit": (str(ln.get("unit") or "").strip() or None),
                    "unit_price": _money(ln.get("unit_price")), "amount": _money(ln.get("amount")),
                    "material_id": None})
    return out


def parse_review(text: str, checklist: Checklist, *, has_previous: bool) -> dict[str, Any]:
    """The reply as findings the page can show. Every check in the checklist is
    present afterwards: one the model left out is added as "not_shown" with the
    reason, rather than vanishing (a dropped check reads as a pass)."""
    data = _json(text)
    raw = [f for f in data.get("findings") or [] if isinstance(f, dict)]
    by_key: dict[str, dict] = {}
    extra: list[dict] = []
    known = {c.key for c in checklist.checks}
    for f in raw:
        k = str(f.get("check_key") or "").strip()
        if k in known and k not in by_key:
            by_key[k] = f
        else:
            extra.append(f)
    ordered = [by_key.get(c.key) or {"check_key": c.key, "check": c.label, "shop": "Not reported",
                                     "plan": "Not reported", "result": "not_shown",
                                     "why": "The review did not report this check.", "ask_who": "none"}
               for c in checklist.checks] + extra
    labels = {c.key: c.label for c in checklist.checks}
    findings = []
    for i, f in enumerate(ordered, 1):
        result = str(f.get("result") or "").lower()
        if result not in RESULTS:
            result = "confirm"
        ask = str(f.get("ask_who") or "none").lower()
        ask = ask if ask in ASK_WHO else "none"
        question = str(f.get("question") or "").strip()
        if result == "match":
            ask, question = "none", ""
        prev = f.get("previous")
        prev = prev if has_previous and prev in PREVIOUS else None
        findings.append({
            "id": f"f{i}", "check_key": f.get("check_key") or f"extra{i}",
            "check": str(f.get("check") or labels.get(f.get("check_key"), "Other")).strip(),
            "shop": str(f.get("shop") or "Not shown").strip(), "shop_ref": str(f.get("shop_ref") or "").strip(),
            "plan": str(f.get("plan") or "Not shown").strip(), "plan_ref": str(f.get("plan_ref") or "").strip(),
            "result": result, "why": str(f.get("why") or "").strip(), "ask_who": ask, "question": question,
            "call": None, "call_note": None, "previous": prev,
        })
    q = data.get("quote") if isinstance(data.get("quote"), dict) else {}
    quote = {}
    if q and any(q.get(k) for k in ("supplier", "number", "total")):
        quote = {"supplier": q.get("supplier"), "number": q.get("number"), "date": q.get("date"),
                 "subtotal": _money(q.get("subtotal")), "tax": _money(q.get("tax")), "total": _money(q.get("total")),
                 "bom_matches": q.get("bom_matches") if isinstance(q.get("bom_matches"), bool) else None,
                 "notes": [str(n) for n in (q.get("notes") or []) if str(n).strip()],
                 "lines": _lines(q.get("lines"))}
    return {"summary": str(data.get("summary") or "").strip(), "findings": findings, "quote": quote}


def counts(findings: list[dict]) -> dict[str, int]:
    out = {r: 0 for r in RESULTS}
    for f in findings:
        out[f["result"]] = out.get(f["result"], 0) + 1
    return out


def is_open(f: dict) -> bool:
    return f["result"] in OPEN_RESULTS and f.get("call") not in ("fine", "answered")


def gate(s) -> dict[str, bool]:
    """The three ticks before ordering. A marker only (Amir, 2026-10-17: "keep as
    marker only"): Keystone never places or blocks an order."""
    no_critical = not any(f["result"] == "critical" and is_open(f) for f in s.findings or [])
    outcome_ok = s.outcome in ("no_exceptions", "as_noted") and s.status != "superseded"
    return {"signed_received": bool(s.signed_received), "no_open_critical": no_critical,
            "outcome_ok": outcome_ok, "cleared": bool(s.signed_received) and no_critical and outcome_ok}


def derive_status(s) -> str:
    if s.status in ("draft", "reviewing", "review_failed", "superseded"):
        return s.status
    if s.outcome:
        return "closed"
    findings = s.findings or []
    if any(f["result"] in OPEN_RESULTS and not f.get("call") for f in findings):
        return "needs_call"
    # Waiting only once EVERY party with questions has had them copied to Gmail.
    asked = {p for p, qs in question_groups(findings).items() if qs}
    copied = {e["party"] for e in s.emails or [] if e.get("copied_at")}
    if asked and asked <= copied:
        return "waiting"
    return "needs_call"


def note(s, by: str, text: str) -> None:
    s.history = [*(s.history or []), {"at": datetime.now(timezone.utc).isoformat(), "by": by, "text": text}]


def question_groups(findings: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for f in findings:
        if f.get("call") == "ask" and f.get("question"):
            who = f["ask_who"] if f["ask_who"] in ("engineer", "supplier") else "supplier"
            groups.setdefault(who, []).append(f)
    return groups


def draft_email(s, party: str, questions: list[dict], *, project: dict, to: dict, sender: dict,
                governing: list[str]) -> dict[str, Any]:
    """The question email, from a template. The PM edits it before copying it out."""
    first = (to.get("name") or "").split()[0] if to.get("name") else ""
    # File names read as titles in an email: no extension (live audit, 2026-10-17).
    names = [re.sub(r"\.(pdf|png|jpe?g|dwg|docx?|xlsx?)$", "", g, flags=re.I) for g in governing]
    against = ", ".join(names) if names else "the project documents"
    owner = (s.supplier + ("'" if s.supplier.endswith("s") else "'s") + " ") if s.supplier else ""
    what = f"{owner}{s.name} ({s.revision})"
    n = len(questions)
    if party == "engineer":
        ask = f"Before we place the order we need your answer on {n} point{'s' if n != 1 else ''}:"
    else:
        ask = f"Before we approve it we need the following {'point' if n == 1 else f'{n} points'} sorted out:"
    body = [f"Hi {first}," if first else "Hi,", "",
            f"We've checked {what} for {project.get('address') or project.get('name')} against {against}. {ask}", ""]
    body += [f"{i}. {q['question']}" for i, q in enumerate(questions, 1)]
    body += ["", "Thanks,", sender.get("name") or "", sender.get("company") or "", sender.get("phone") or ""]
    subject = f"{project.get('address') or project.get('name')} · {s.name} {s.revision}: " \
              f"{n} question{'s' if n != 1 else ''}"
    return {"party": party, "to_name": to.get("name") or "", "to_email": to.get("email") or "",
            "subject": subject, "body": "\n".join(x for x in body).rstrip() + "\n",
            "finding_ids": [q["id"] for q in questions], "copied_at": None}
