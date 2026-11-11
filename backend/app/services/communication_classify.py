"""AI classification for project communications (Conversations feature).

Cheap, single-shot Haiku call that tags a logged communication with a
construction-taxonomy category and extracts a summary, action items, urgency,
and whether it contains a trade quote. Best-effort: on any failure the caller
stores the raw communication uncategorized (processed_at stays NULL).
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.models.project_communication import COMMUNICATION_CATEGORIES
from app.services.ai import MODEL_HAIKU, call_anthropic


_SYSTEM = (
    "You classify a single construction-project communication (an email, message, "
    "call note, or meeting note between a contractor and a client, trade, or authority) "
    "for a project-management log. Respond with ONLY a JSON object, no prose.\n\n"
    "Categories (choose the single best fit):\n"
    "- general: day-to-day correspondence, admin, scheduling a call, access, neighbours\n"
    "- rfi: a question / request for information / clarification on drawings, specs, or method\n"
    "- selection: material/finish/fixture selection or approval decision\n"
    "- quote: a price quote or bid from a trade/subcontractor/supplier\n"
    "- change_order: a request for extra work or a credit, or an approved change\n"
    "- schedule: timeline, progress, look-ahead, start/finish dates, delays\n"
    "- financial: invoicing, draws, payments, deposits, budget\n"
    "- deficiency: a defect, punch-list item, complaint, or issue with completed work\n"
    "- permit_inspection: permits, inspections, or authority-having-jurisdiction matters\n"
    "- meeting: meeting minutes, decisions, action items from a discussion\n"
    "- warranty: warranty claims, service requests, post-completion issues\n"
    "- other: none of the above\n\n"
    "You also EXTRACT any trade/subcontractor/supplier contacts mentioned — the "
    "sender if they are a trade (not the homeowner client and not us, the "
    "contractor), plus any trade referenced in the body with enough identifying "
    "detail. Do NOT extract the homeowner/client, our own company, or a bare name "
    "with no trade context. Only include a contact when the type of trade is "
    "reasonably clear.\n"
    "trade_category must be one of: excavation, concrete, framing, roofing, "
    "windows_doors, insulation, drywall, plumbing, electrical, hvac, flooring, "
    "tile, painting, cabinets_millwork, masonry, waterproofing, landscaping, "
    "fire_protection, low_voltage, demolition, cleaning, disposal, engineering, "
    "architecture, survey, supplier, other.\n\n"
    "JSON shape:\n"
    "{\n"
    '  "category": "<one of the categories>",\n'
    '  "summary": "<1-2 sentence plain-language summary>",\n'
    '  "action_items": ["<short actionable item>", ...],   // [] if none\n'
    '  "urgency": "low" | "normal" | "high",\n'
    '  "direction": "inbound" | "outbound" | "internal",   // inbound = from client/trade to us\n'
    '  "has_quote": true | false,                           // true if it contains a trade/supplier price\n'
    '  "trades": [                                          // [] if none clearly identifiable\n'
    '    {\n'
    '      "company_name": "<company or null>",\n'
    '      "contact_name": "<person or null>",\n'
    '      "trade_category": "<one category from the list>",\n'
    '      "email": "<email or null>",\n'
    '      "phone": "<phone or null>"\n'
    '    }\n'
    '  ]\n'
    "}"
)


def _extract_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


def classify_communication(subject: str | None, body: str, from_email: str | None = None) -> dict[str, Any]:
    """Return {category, summary, action_items, urgency, direction, has_quote}.

    Raises on API/parse failure — the caller decides how to degrade.
    """
    content = (
        (f"Subject: {subject}\n" if subject else "")
        + (f"From: {from_email}\n" if from_email else "")
        + "\n" + (body or "")[:8000]
    )
    res = call_anthropic(
        model=MODEL_HAIKU,
        system=_SYSTEM,
        messages=[{"role": "user", "content": content}],
        max_tokens=600,
    )
    data = _extract_json(res.text)

    cat = str(data.get("category") or "other")
    if cat not in COMMUNICATION_CATEGORIES:
        cat = "other"
    urgency = str(data.get("urgency") or "normal")
    if urgency not in ("low", "normal", "high"):
        urgency = "normal"
    items = data.get("action_items")
    action_items = [str(x) for x in items][:10] if isinstance(items, list) else []
    direction = str(data.get("direction") or "").strip() or None
    if direction not in ("inbound", "outbound", "internal", None):
        direction = None

    raw_trades = data.get("trades")
    trades: list[dict[str, Any]] = []
    if isinstance(raw_trades, list):
        for t in raw_trades[:5]:
            if not isinstance(t, dict):
                continue
            company = (t.get("company_name") or "").strip() or None
            contact = (t.get("contact_name") or "").strip() or None
            if not (company or contact):
                continue  # need at least one identifier
            trades.append({
                "company_name": company,
                "contact_name": contact,
                "trade_category": (t.get("trade_category") or "other").strip().lower(),
                "email": (t.get("email") or "").strip() or None,
                "phone": (t.get("phone") or "").strip() or None,
            })

    return {
        "category": cat,
        "summary": (str(data.get("summary")).strip() or None) if data.get("summary") else None,
        "action_items": action_items,
        "urgency": urgency,
        "direction": direction,
        "has_quote": bool(data.get("has_quote")),
        "trades": trades,
    }
