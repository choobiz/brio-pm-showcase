"""AI-assisted CPM schedule derivation FROM an estimate version.

The model proposes an activity network (codes, durations in working days,
predecessors) grounded in the estimate's work; `cpm.compute_cpm` then does the
forward/backward pass, floats, critical path, and dates. The estimate is the
source of truth — no activities for work absent from the estimate.
"""
from __future__ import annotations

import json

_SYSTEM = """You are a senior construction scheduler at BRIO building a CPM schedule for the work in a finalized estimate. The ESTIMATE is the source of truth.

RULES:
- Create activities ONLY for work present in the estimate/scope provided. Do not invent trades or systems that aren't there.
- Sequence realistically for a small commercial/residential build: permits & mobilization → site/excavation & stormwater → foundation & concrete → framing → roofing/envelope → windows/doors → MEP rough-in → insulation & drywall → finishes (flooring, paint, millwork) → fixtures & trim → final inspections & closeout. Only include phases that exist in the estimate.
- Give a single most-likely `duration_wd` per activity in WORKING DAYS (5-day weeks). Do NOT pad for risk — the system derives the optimistic/pessimistic spread and the P80 finish automatically.
- Model REALISTIC PARALLELISM. Do NOT chain every activity into one serial line. Trade-independent work runs concurrently: MEP rough-ins (electrical / plumbing / mechanical) proceed in parallel once the space is framed; finish trades (flooring, tile, painting, millwork, trim) overlap across different rooms/areas. Only add a predecessor when one activity GENUINELY blocks another — a structural/sequence dependency (can't frame before foundation), an inspection gate, or a weathertight gate. A schedule where most activities land on the critical path is wrong: aim for a critical path of true dependencies with real float on the rest.
- Use short activity codes (A10, A20, …). predecessor_codes lists the codes that must finish before an activity starts.
- Include a start milestone (permit/mobilization, duration 0) and an end milestone (Substantial Completion, duration 0). Mark key gates as milestones.

Return ONLY valid JSON (no prose/fences) of this exact shape:
{ "activities": [ { "code": "A10", "name": "<activity>", "phase": "<phase>", "duration_wd": <int>, "predecessor_codes": ["<code>", ...], "is_milestone": <bool> } ] }
Escape quotes/newlines inside strings so it parses."""


def build_schedule_from_estimate_messages(estimate_payload: dict, scope_payload: dict, project: dict) -> tuple[str, list[dict], int]:
    cats = [c.get("name") for c in (estimate_payload.get("categories") or []) if isinstance(c, dict) and c.get("name")]
    data = {
        "project": {"name": project.get("name"), "type": project.get("type"), "sqft": project.get("sqft")},
        "estimateCategories": cats,
        "scopeSummary": (scope_payload or {}).get("summary"),
    }
    user = ("Build the CPM schedule for the work in this estimate.\n\n" + json.dumps(data, indent=1, default=str))
    return _SYSTEM, [{"role": "user", "content": user}], 8000
