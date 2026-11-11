"""Client-package fingerprint — detect when a sent package has gone stale.

Hashes each package-affecting input into its own section so the UI can name
exactly what changed (schedule dates, tasks, contract amount, payment terms,
discussion, selections, project info). Mirrors the inputs that
`package_pdf.build_client_package` actually renders — keep the two in sync.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# Section key → human label shown in the stale-package banner.
SECTION_LABELS = {
    "schedule_dates": "Schedule dates (start / finish)",
    "schedule_tasks": "Schedule tasks",
    "contract_amount": "Contract amount",
    "payment_terms": "Payment terms",
    "discussion": "Discussion summary",
    "material_selections": "Material selections / estimate",
    "project_info": "Project info",
}


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _h(obj: Any) -> str:
    """Stable short hash of any JSON-able object."""
    blob = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def compute_package_fingerprint(db: Session, project_id: str) -> dict[str, Any]:
    """Return {sections:{name:hash}, details:{...}} for the project's package."""
    # ── active estimate → contract amount, payment terms, selections ──────────
    erow = db.execute(
        text("select id, reference, version_number, payload from estimate_versions "
             "where project_id=:p and is_active=true"),
        {"p": project_id},
    ).mappings().first()

    contract_amount: dict[str, Any] = {}
    payment_terms: list[dict] = []
    selections: Any = None
    if erow:
        est = erow["payload"]
        est = est if isinstance(est, dict) else json.loads(est)
        oh, pr = _num(est.get("overhead")), _num(est.get("profit"))
        base = sum(_num(c.get("total")) for c in est.get("categories", []) if not c.get("optional"))
        pretax = round(base * (1 + oh / 100) * (1 + pr / 100))
        total = round(pretax * 1.05)
        contract_amount = {"pretax": pretax, "total": total}
        # Payment draws are derived from the total via the static draw weights.
        from app.services.package_pdf import _DRAW_WEIGHTS
        acc = 0
        for i, (m, pct, _d) in enumerate(_DRAW_WEIGHTS):
            amt = total - acc if i == len(_DRAW_WEIGHTS) - 1 else round(total * pct / 100)
            acc += amt
            payment_terms.append({"milestone": m, "pct": pct, "amount": amt})
        # Selections: estimate identity + the category/item structure the client sees.
        selections = {
            "ref": erow["reference"], "ver": erow["version_number"],
            "categories": [
                {"name": c.get("name"), "optional": bool(c.get("optional")),
                 "items": [{"name": it.get("name"), "materials": it.get("materials")}
                           for it in (c.get("items") or [])]}
                for c in est.get("categories", [])
            ],
        }

    # ── latest schedule → dates + tasks ───────────────────────────────────────
    srow = db.execute(
        text("select id, project_start, p50_finish_date, p80_finish_date from schedules "
             "where project_id=:p and client_visible order by created_at desc limit 1"),
        {"p": project_id},
    ).mappings().first()
    schedule_dates: dict[str, Any] = {}
    schedule_tasks: list[dict] = []
    if srow:
        schedule_dates = {
            "start": str(srow["project_start"]),
            "p50": str(srow["p50_finish_date"]),
            "p80": str(srow["p80_finish_date"]),
        }
        arows = db.execute(
            text("select activity_code, name, expected_wd from activities "
                 "where schedule_id=:s order by activity_code"),
            {"s": srow["id"]},
        ).mappings().all()
        schedule_tasks = [
            {"code": a["activity_code"], "name": a["name"], "wd": _num(a["expected_wd"])}
            for a in arows
        ]

    # ── project fields → discussion + info ────────────────────────────────────
    prow = db.execute(
        text("select name, address, type, sqft, discussion_points, home_warranty_required "
             "from projects where id=:p"),
        {"p": project_id},
    ).mappings().one()
    discussion = prow["discussion_points"] or []
    project_info = {"name": prow["name"], "address": prow["address"],
                    "type": prow["type"], "sqft": prow["sqft"],
                    "home_warranty_required": bool(prow["home_warranty_required"])}

    sections = {
        "schedule_dates": _h(schedule_dates),
        "schedule_tasks": _h(schedule_tasks),
        "contract_amount": _h(contract_amount),
        "payment_terms": _h(payment_terms),
        "discussion": _h(discussion),
        "material_selections": _h(selections),
        "project_info": _h(project_info),
    }
    return {
        "sections": sections,
        "details": {
            "schedule_dates": schedule_dates,
            "contract_total": contract_amount.get("total"),
            "n_tasks": len(schedule_tasks),
        },
    }


def diff_fingerprints(baseline_sections: dict, current_sections: dict) -> list[str]:
    """Section keys that changed between a baseline and the current fingerprint."""
    keys = set(baseline_sections) | set(current_sections)
    return sorted(k for k in keys if baseline_sections.get(k) != current_sections.get(k))
