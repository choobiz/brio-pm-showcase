"""Which records an instruction may edit, how to read one, and how to write it.

⚠️ EXTRACTED FROM routers/ai_edits.py, NOT COPIED. Proposals need the same three
things the ai_edit apply path needs — the allow-list of editable tables, the
current document, and a write that re-derives the totals the model is forbidden
to touch. A second copy would be two places that must agree about what is
editable and what "locked" means, and they would eventually disagree, the way
two copies of a coordinate transform elsewhere in this product already can. The
router imports these back, so its behaviour is unchanged.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text as _T
from sqlalchemy.orm import Session


#: Where each editable record keeps its state, and whether editing is allowed
#: once it has left the building. A sent estimate or a decided change order is
#: evidence of what was agreed; changing one in place rewrites history.
TARGETS: dict[str, dict[str, Any]] = {
    "scope": {"table": "scopes", "column": "payload", "locked": ()},
    "estimate": {"table": "estimates", "column": "payload",
                 "locked": ("sent", "accepted")},
    # The workbench surface. estimate_versions has no status column — an
    # estimate version is never "sent", it is superseded by the next one — so
    # nothing locks. The active version is flagged as a warning instead, since
    # invoices bill from it and editing it changes what is being billed.
    "estimate_version": {"table": "estimate_versions", "column": "payload",
                         "locked": ()},
    "change_order": {"table": "change_orders", "column": None,
                     "locked": ("approved", "rejected", "void")},
}


def _current_document(db: Session, spec: dict[str, Any], record_id: str) -> dict[str, Any]:
    if spec["column"] is not None:
        row = db.execute(
            _T(f"SELECT {spec['column']} AS doc FROM {spec['table']} WHERE id = :i"),
            {"i": record_id},
        ).mappings().first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="record not found")
        return dict(row["doc"] or {})
    from app.services.revisions import _current_state

    state = _current_state(db, spec["table"], record_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="record not found")
    return state


def _write(
    db: Session, spec: dict[str, Any], record_id: str, document: dict[str, Any]
) -> dict[str, Any]:
    """Persist the edited document and re-derive anything computed from it.

    The model is forbidden from touching totals, so after ANY edit the derived
    money has to be recomputed here. Without this, correcting a quantity would
    leave a line that says one thing and a total that says another — which is
    exactly the defect the change-order editor was fixed for earlier.
    """
    import json

    recomputed: dict[str, Any] = {}

    if spec["column"] is not None:
        if spec["table"] in ("estimates", "estimate_versions"):
            # The model cannot write a total, so the category rollups have to be
            # re-derived here — otherwise correcting one line item's labour cost
            # leaves a category total that no longer equals the lines under it.
            from app.services.estimate_totals import project_contingency, rollup_payload

            # With the division set, so an edit cannot re-add contingency to a
            # division the set says carries none.
            pid = db.execute(_T(f"SELECT project_id FROM {spec['table']} WHERE id = :i"),
                             {"i": record_id}).scalar()
            recomputed.update(rollup_payload(document, project_contingency(db, pid) if pid else None))
        db.execute(
            _T(f"UPDATE {spec['table']} SET {spec['column']} = CAST(:v AS jsonb) "
               "WHERE id = :i"),
            {"v": json.dumps(document, default=str), "i": record_id},
        )
        return recomputed

    # change_orders — a whole-row record. Its cost_impact is the sum of the
    # line items and is never typed, here or anywhere else.
    detail = document.get("detail") or {}
    items = [i for i in (detail.get("line_items") or []) if isinstance(i, dict)]
    total = Decimal("0")
    for item in items:
        try:
            total += Decimal(str(item.get("amount") or 0))
        except (InvalidOperation, TypeError, ValueError):
            continue
    if items:
        recomputed["cost_impact"] = float(total)

    db.execute(
        _T("UPDATE change_orders SET title = :t, description = :d, "
           "schedule_impact_days = :days, detail = CAST(:detail AS jsonb), "
           "cost_impact = CASE WHEN :has_items THEN :total ELSE cost_impact END "
           "WHERE id = :i"),
        {
            "t": document.get("title"),
            "d": document.get("description"),
            "days": int(document.get("schedule_impact_days") or 0),
            "detail": json.dumps(detail, default=str),
            "has_items": bool(items),
            "total": total,
            "i": record_id,
        },
    )
    return recomputed
