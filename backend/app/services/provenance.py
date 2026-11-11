"""Where a quantity came from — and, just as importantly, where the trail stops.

⚠️ THE RULE THIS MODULE EXISTS TO ENFORCE. A link that is not recorded is
reported as NOT RECORDED. Never as zero, never as "none", never omitted. An
earlier draft of the workspace design showed a rate as "used by 0 estimate
lines" when the truth was that nothing records which rate produced a line — a
fabrication dressed as a measurement, and exactly the failure that makes an
assistant untrustworthy around numbers.

What is genuinely available today, measured against production:

    quantity -> document          49 of 50 rows
    quantity -> machine's value   50 of 50 rows
    quantity -> who reviewed it    7 of 50 rows (matches 7 reviewed)
    quantity -> sheet / page       0 of 50 rows — CAD has no pages
    quantity -> region on sheet    not stored anywhere (Phase 2 for PDF)
    estimate line -> quantity      not stored anywhere (Phase 3)

So the honest answer names the drawing, explains the method, states the standing
and then says plainly what it cannot show — with something the reader can
actually do instead.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

#: Shown verbatim wherever a trail stops. One phrase, so it is recognisable.
NOT_RECORDED = "not recorded"

#: How each source was produced, in words an estimator would use.
_METHOD = {
    "cad_extract": (
        "Read from the CAD model. Areas come from tracing the wall faces and taking "
        "the enclosed area — not a rectangle drawn around the building. Counts come "
        "from the blocks placed on the drawing."
    ),
    "manual_measure": (
        "Measured by hand on the drawing, using the page scale in force at the time."
    ),
    "manual_entry": "Typed in directly.",
    "candidate_confirmed": "Accepted from a suggestion made by reading the drawing.",
}

#: Plain-language review standing.
_STANDING = {
    "candidate": "Not yet reviewed — this is what the machine read, and nobody has checked it.",
    "confirmed": "Reviewed and accepted as read.",
    "corrected": "Reviewed and corrected — a person replaced the machine's number.",
    "excluded": "Marked as not counted. It does not contribute to any price.",
}

#: Areas are verified; perimeter is not. Stated on the number, not in a footnote.
_LENGTH_UNITS = ("lf", "ft", "m", "lm")


def quantity_provenance(db: Session, quantity_id: UUID) -> dict[str, Any] | None:
    """Everything known about one quantity, including what is not known."""
    r = db.execute(_T("""
        SELECT q.id, q.project_id, p.name AS project_name, q.discipline, q.quantity_type,
               q.value, q.unit, q.source, q.status, q.machine_value, q.notes,
               q.source_document_id, q.source_page, q.source_candidate_key,
               q.source_bbox, q.source_revision_label,
               q.confirmed_at, u.name AS confirmed_by_name, u.email AS confirmed_by_email,
               d.filename AS document_name, d.category AS document_category
          FROM quantity_overrides q
          JOIN projects p ON p.id = q.project_id
     LEFT JOIN users u ON u.id = q.confirmed_by_id
     LEFT JOIN documents d ON d.id = q.source_document_id
         WHERE q.id = :qid"""), {"qid": str(quantity_id)}).mappings().first()
    if r is None:
        return None

    machine = float(r["machine_value"]) if r["machine_value"] is not None else None
    value = float(r["value"]) if r["value"] is not None else None
    corrected = r["status"] == "corrected" and machine is not None and value != machine

    # The analysis this project's CAD reading was signed off under, if any.
    pack = db.execute(_T("""
        SELECT version_number, status, approved_at, payload->'accuracy'->>'headline' AS headline
          FROM drawing_analysis_packs WHERE project_id = :p
      ORDER BY version_number DESC LIMIT 1"""), {"p": str(r["project_id"])}).mappings().first()

    limits: list[str] = []
    if r["source_page"] is None:
        limits.append(
            "I can name the drawing but not the sheet. CAD models have no pages, so there is "
            "no sheet number to record."
            if (r["source"] == "cad_extract")
            else "The sheet this came from was not recorded."
        )
    # Only a limit when there is genuinely nothing to point at. Saying "I cannot
    # show you where" while holding the coordinates would be false modesty, and
    # a reader who learns to skip this panel will skip it when it matters.
    if not r["source_bbox"]:
        limits.append(
            "I cannot point at the part of the drawing this was measured from. "
            + ("A CAD measurement has no position on a printed sheet, so the way to "
               "check this one is to open the drawing and compare the outline."
               if r["source"] == "cad_extract" else
               "It was not recorded when this was confirmed.")
        )
    if (r["unit"] or "").lower() in _LENGTH_UNITS:
        limits.append(
            "This is a length. Areas from this process are verified; lengths are read rather "
            "than measured and have not been. Check it against the sheet before pricing."
        )

    return {
        "id": str(r["id"]),
        "project": {"id": str(r["project_id"]), "name": r["project_name"]},
        "what": {
            "measurement": r["quantity_type"],
            "discipline": r["discipline"],
            "value": value,
            "unit": r["unit"],
        },
        "standing": {
            "state": r["status"],
            "plain": _STANDING.get(r["status"], r["status"]),
            "reviewed_by": r["confirmed_by_name"] or r["confirmed_by_email"] or NOT_RECORDED,
            "reviewed_at": r["confirmed_at"].date().isoformat() if r["confirmed_at"] else NOT_RECORDED,
            "may_be_priced": r["status"] in ("confirmed", "corrected"),
        },
        "machine_reading": {
            "value": machine if machine is not None else NOT_RECORDED,
            "differs_from_value": corrected,
            "note": (
                f"A person replaced the machine's {machine} with {value}. Both are kept so the "
                "gap stays visible." if corrected else
                "The value in force is what the machine read."
            ),
        },
        "drawing": {
            "name": r["document_name"] or NOT_RECORDED,
            "id": str(r["source_document_id"]) if r["source_document_id"] else NOT_RECORDED,
            "filed_as": r["document_category"] or NOT_RECORDED,
            "sheet": r["source_page"] if r["source_page"] is not None else NOT_RECORDED,
            # [x0, y0, x1, y1] in PDF user-space units, pre-rotation. The client
            # rotates it to match the canvas; see the column comment.
            "region": r["source_bbox"] or NOT_RECORDED,
            "revision_read": r["source_revision_label"] or NOT_RECORDED,
        },
        "method": _METHOD.get(r["source"], r["source"]),
        "analysis": {
            "version": pack["version_number"] if pack else NOT_RECORDED,
            "state": pack["status"] if pack else NOT_RECORDED,
            "signed_off": pack["approved_at"].date().isoformat()
                          if (pack and pack["approved_at"]) else NOT_RECORDED,
            "confidence": (pack["headline"] if pack and pack["headline"] else NOT_RECORDED),
        },
        # Deliberately not an empty list or a zero. Nothing records which estimate
        # line consumed which quantity, so the answer is unknown.
        "used_in_estimates": NOT_RECORDED,
        "limits": limits,
        "notes": r["notes"] or NOT_RECORDED,
    }
