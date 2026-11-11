"""Drafting the email that shares estimate packages with a client.

Amir, 2026-10-27, sending 456 W Sample' Suite 100 and corridor estimates: the
assistant drafts the email from what the project holds, Amir reads and edits it,
and sends it himself. Keystone never sends it.

The email describes the "scope + selections + lump sum" package of each chosen
estimate. It is a proposal, not an award: it never mentions or offers a contract
("No need to send the contract when sharing the estimate since the project is
not given to us"). Figures come from estimate_selection_pdf.client_totals, the
same function the PDF prints from, and check_draft flags a draft that quotes a
figure the PDFs don't show or uses internal wording.
"""
from __future__ import annotations

import json
import re
from typing import Any

#: 2026-10-27 first live run on 456: 2,000 was used up and the JSON never
#: closed. A 300-word email is ~500 tokens; leave room for the JSON and drift.
MAX_TOKENS = 4000
PURPOSE = "estimate_share"

SYSTEM = """You draft the email a general contractor's owner (BRIO Construction, Vancouver, BC)
sends a client to share one or more estimate packages. Each package is a PDF with three
parts: the scope of work, the material selections WITHOUT prices for the client to confirm
(mark OK or name the product they prefer), and every line of the estimate with one
lump-sum price.

Write as the sender, in plain, warm, professional words: greeting by the contact's first
name when CONTEXT has it, otherwise the placeholder [name]. Thank them briefly if
CONTEXT mentions a site visit. Then list each package with its lump-sum price, written
exactly as given in CONTEXT as "$X + GST ($Y including GST)". Say what each package
contains and ask them to confirm the material selections and send them back; a different
product may change the price, confirmed in writing before ordering. Give a few short notes
per package from CONTEXT (what is included, the main assumptions, exclusions or
allowances). Offer to walk through it by phone or on site. Close with the sender's name,
company and phone.

Rules:
- Use ONLY facts in CONTEXT. Anything missing becomes a bracketed placeholder like
  [name] or [date]. Never invent names, dates, schedules or terms.
- Quote no dollar figure other than the lump-sum figures in CONTEXT.
- Never mention costs, rates, markup, overhead, profit, margin, contingency amounts,
  internal version numbers or internal notes.
- Never mention, offer or attach a contract or agreement: this is a proposal, the work
  has not been awarded.
- No em dashes. No marketing tone, no filler, no "I hope this finds you well".
- Keep the body under 300 words: short paragraphs, notes as brief bullets.
- Mention as attached only the package PDFs listed in CONTEXT.attachments.

Respond with JSON only: {"subject": "...", "body": "..."}. The subject is short and starts
with the site address."""


def _scope_points(payload: dict[str, Any], limit: int = 6) -> dict[str, list[str]]:
    def lst(key: str) -> list[str]:
        v = payload.get(key) or []
        return [str(x).strip() for x in v if str(x).strip()][:limit] if isinstance(v, list) else []
    return {"assumptions": lst("assumptions"), "exclusions": lst("exclusions")}


def package_context(version: dict[str, Any], scope: dict[str, Any] | None) -> dict[str, Any]:
    """One package as the drafter sees it. ``version`` holds payload + reference;
    ``scope`` the scope payload (or None)."""
    from app.services.estimate_selection_pdf import client_totals, selection_items

    payload = version.get("payload") or {}
    t = client_totals(payload)
    sel = selection_items(payload)
    est_points = _scope_points(payload)
    scope_points = _scope_points(scope or {})
    return {
        "name": version.get("title") or version.get("reference"),
        "lump_sum_before_gst": _money(t["subtotal"]),
        "lump_sum_including_gst": _money(t["total"]),
        "scope_summary": ((scope or {}).get("summary") or "")[:1200],
        "material_selections_to_confirm": len(sel),
        "selection_groups": sorted({s["group"] for s in sel}),
        "assumptions": (est_points["assumptions"] or scope_points["assumptions"]),
        "exclusions": (est_points["exclusions"] or scope_points["exclusions"]),
    }


def _money(v: float) -> str:
    return f"${v:,.2f}"


def build_messages(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], int]:
    ctx = payload.get("context") or {}
    user = ["TASK: Draft the email sharing these estimate packages with the client.",
            "CONTEXT (JSON):", json.dumps(ctx, default=str, indent=1)[:24000]]
    extra = (payload.get("instructions") or "").strip()
    if extra:
        user.append(f"THE SENDER ADDS: {extra}")
    return SYSTEM, [{"role": "user", "content": "\n".join(user)}], MAX_TOKENS


_INTERNAL = ("markup", "overhead", "profit", "margin", "contingency", "unit rate", "cost basis")
_CONTRACT = re.compile(r"\b(contract|agreement)s?\b", re.I)
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")


def check_draft(draft: dict[str, str], ctx: dict[str, Any]) -> list[str]:
    """Things Amir should look at before sending. Empty when nothing stands out."""
    text = f"{draft.get('subject', '')}\n{draft.get('body', '')}"
    low = text.lower()
    warnings: list[str] = []
    allowed = set()
    for p in ctx.get("packages") or []:
        for k in ("lump_sum_before_gst", "lump_sum_including_gst"):
            allowed.add(p[k])
            allowed.add(p[k].replace(".00", ""))
        if p["lump_sum_before_gst"] not in text and p["lump_sum_before_gst"].replace(".00", "") not in text:
            warnings.append(f"The price for {p['name']} ({p['lump_sum_before_gst']} + GST) is not in the email.")
    stray = sorted({m.group(0).replace(" ", "") for m in _MONEY.finditer(text)} - allowed)
    if stray:
        warnings.append("Dollar figures that are not in the packages: " + ", ".join(stray) + ".")
    words = [w for w in _INTERNAL if w in low]
    if words:
        warnings.append("Internal wording to check: " + ", ".join(words) + ".")
    if _CONTRACT.search(text):
        warnings.append("It mentions a contract or agreement; the work has not been awarded.")
    if "—" in text:
        warnings.append("It contains an em dash.")
    if re.search(r"\[[^\]]+\]", text):
        warnings.append("Fill in the [placeholders] before sending.")
    return warnings
