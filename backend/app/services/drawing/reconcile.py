"""Macro-level reconciliation of a CAD takeoff against the PDF permit set.

Cross-checks the CAD-derived totals (floor area, openings) against the values the
architect STATES in the drawings. This is the step that would have instantly caught
the 1234 Example floor-area double-count — CAD said 1,336 sf, the PDF says in plain
text "668 sq ft proposed" (distinct from the "669 sq ft allowable" zoning number).

Deliberately macro-only: total area, floor count, door/window schedule counts. The
goal is a sanity gate, not a second full takeoff — if the macros disagree, the
detailed quantities are not to be trusted until a human reconciles them.
"""
from __future__ import annotations

import json
import re
from typing import Any

_MACRO_SYSTEM = (
    "You read architectural permit drawings and extract macro building metrics "
    "precisely. The PROPOSED BUILDING floor area is the ONLY figure that counts as "
    "total_proposed_area_sf. You must NEVER report a zoning ALLOWABLE / ALLOWED / "
    "MAXIMUM / PERMITTED / lot-coverage / setback / rear-yard number as the proposed "
    "area — those are legal limits, not the building. When a drawing states both "
    "(e.g. '668 sq ft proposed' alongside '669 sq ft allowable'), the PROPOSED "
    "figure is the answer, full stop."
)

_MACRO_PROMPT = """From the text of this architectural drawing set, extract the building's MACRO metrics.

Return JSON only (no prose):
{{
  "total_proposed_area_sf": <number or null>,
  "floor_count": <integer or null>,
  "door_count": <integer or null>,
  "window_count": <integer or null>,
  "area_basis": "<exact source phrase — MAX 12 words, no explanation>",
  "notes": "<MAX 15 words>"
}}

Keep area_basis and notes SHORT (a phrase, not a paragraph) or the JSON will be truncated.

Field rules:
- total_proposed_area_sf — the PROPOSED building floor area ONLY, decided in this order:
    1. If a "proposed" building / floor / GFA area is stated, use that exact number.
    2. Else if it is given as proposed components (e.g. "garage 399.6 sf" + "office 268 sf"),
       SUM the proposed components.
    3. NEVER use an "allowable" / "allowed" / "maximum" / "permitted" / "lot coverage" /
       "FSR" figure — those are zoning limits, not the building.
    4. If only allowable figures exist and no proposed area can be determined, return null.
  Put the exact source phrase in "area_basis" so the choice is auditable.
- floor_count — number of finished storeys/levels (count distinct finished-floor / FF elevations).
- door_count / window_count — from a door / window (glazing) SCHEDULE if one exists, else null.

DRAWING TEXT:
{text}"""


def extract_pdf_macros(pdf_text: str, call_fn, model: str) -> dict[str, Any]:
    """Ask the model for the drawing's stated macro metrics. Returns {} on failure."""
    if not (pdf_text or "").strip():
        return {}
    prompt = _MACRO_PROMPT.replace("{text}", pdf_text[:14000])
    try:
        result = call_fn(model=model, system=_MACRO_SYSTEM,
                         messages=[{"role": "user", "content": prompt}], max_tokens=1200)
    except Exception:
        return {}
    raw = (result.text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)  # strip a leading markdown fence
    raw = re.sub(r"\s*```$", "", raw)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except (ValueError, TypeError):
        return {}


def _cmp(metric: str, cad, ref, tol_pct: float) -> dict[str, Any]:
    if cad is None or ref is None:
        return {"metric": metric, "cad": cad, "reference": ref,
                "status": "no_reference" if cad is not None else "no_data", "pct_diff": None}
    cad, ref = float(cad), float(ref)
    pct = None if ref == 0 else round(abs(cad - ref) / ref * 100, 1)
    status = "match" if (pct is not None and pct <= tol_pct) else "MISMATCH"
    return {"metric": metric, "cad": cad, "reference": ref, "status": status, "pct_diff": pct}


def reconcile(takeoff, macros: dict[str, Any], project_sqft=None) -> dict[str, Any]:
    """Compare the CAD takeoff macros against the PDF-stated values (+ project meta)."""
    derived = takeoff.derived or {}
    counts = takeoff.counts or {}
    checks: list[dict[str, Any]] = []

    checks.append(_cmp("total_area_sf", derived.get("floor_area_sf"),
                       macros.get("total_proposed_area_sf"), 5.0))
    if project_sqft:
        checks.append(_cmp("total_area_sf_vs_project", derived.get("floor_area_sf"),
                           project_sqft, 10.0))
    checks.append(_cmp("door_count", sum((counts.get("door") or {}).values()),
                       macros.get("door_count"), 0.0))
    checks.append(_cmp("window_count", sum((counts.get("window") or {}).values()),
                       macros.get("window_count"), 0.0))
    # a floor plan can't reveal storey count — record the PDF value for human awareness
    checks.append({"metric": "floor_count", "cad": None,
                   "reference": macros.get("floor_count"),
                   "status": "reference_only", "pct_diff": None})

    mismatches = [c for c in checks if c["status"] == "MISMATCH"]
    return {
        "overall": "flagged" if mismatches else "pass",
        "checks": checks,
        "flags": [f"{c['metric']}: CAD {c['cad']} vs PDF {c['reference']}"
                  f" ({c['pct_diff']}% off)" for c in mismatches],
        "pdf_macros": macros,
    }
