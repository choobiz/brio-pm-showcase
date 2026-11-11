"""Build and approve a project's drawing-analysis pack.

The gate's first phase gave each quantity a status. This is the review those
statuses belong to: what was read, from which documents, how accurate the set is,
and — once someone signs it — permission for an estimate to price it.

Building is deliberately re-runnable. A new drawing arrives, you rebuild, and any
quantity whose measured value CHANGED loses its confirmation (see
`takeoff_to_overrides`). Quantities that did not change keep theirs, because
re-reviewing forty unchanged numbers to get at the two that moved is how a review
stops being done at all.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

CAD_EXTS = (".dwg", ".dxf")


def _cad_documents(db: Session, project_id: UUID) -> list[dict]:
    """Every CAD document on the project, regardless of how it was filed.

    See IS_CAD_SQL — filtering on `category` here meant a DWG filed under any
    other label was never analysed, and nothing said so.
    """
    from app.services.estimate_readiness import IS_CAD_SQL

    rows = db.execute(_T(
        "SELECT id, filename, r2_key FROM documents "
        "WHERE project_id = :p AND archived = false "
        "AND {cad} "
        "ORDER BY filename".format(cad=IS_CAD_SQL)),
        {"p": str(project_id)}).mappings().all()
    return [dict(r) for r in rows]


def _dedupe_formats(docs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split CAD documents into the ones to read and the format duplicates.

    A drawing uploaded as both `Plan.dwg` and `Plan.dxf` is ONE sheet. Sheets
    are additive downstream, so reading both would report twice the floor area
    of a building — a worse answer than reading neither. DXF wins: the DWG path
    converts to DXF anyway, and the conversion is the step that fails.
    """
    groups: dict[str, list[dict]] = {}
    for d in docs:
        stem = os.path.splitext(d["filename"] or "")[0].strip().lower()
        groups.setdefault(stem, []).append(d)

    keep, dupes = [], []
    for members in groups.values():
        if len(members) == 1:
            keep.extend(members)
            continue
        members.sort(key=lambda m: 0 if (m["filename"] or "").lower().endswith(".dxf") else 1)
        chosen, rest = members[0], members[1:]
        keep.append(chosen)
        for r in rest:
            dupes.append({**r, "duplicate_of": chosen["filename"]})
    return keep, dupes


#: Sheet types that must NOT contribute measured quantities, whatever they
#: appear to contain.
#:
#: A building section is a vertical cut. Rasterising it finds closed regions and
#: reports them as "rooms", so on the Maple set six section sheets between
#: them produced ~14,800 sf of floor area — and because sheets SUM, that would
#: have been added to the real plans rather than obviously replacing them. An
#: elevation fails the same way. A site plan fails differently but just as
#: badly: it shows the same building the floor plan does, so counting both
#: double-counts the footprint.
#:
#: Anything unclassified is still measured. A sheet named only "Plan" is what an
#: architect's own export is usually called, and refusing to measure it would
#: cost far more than the noise it might admit.
_NOT_MEASURABLE = {
    "section": "a section is a vertical cut — it has no floor area to measure",
    "elevation": "an elevation is a vertical view — it has no floor area to measure",
    "schedule": "a schedule is a table, not geometry",
    "site_plan": "a site plan shows the same building the floor plan does; "
                 "measuring both would double-count the footprint",
}


def _supersede(db: Session, project_id: UUID, dupe: dict) -> int:
    """Retire quantities an earlier run wrote from a drawing we now skip.

    An earlier pack read both `Plan.dwg` and `Plan.dxf` and wrote a row for
    each. Sheets sum, so leaving both would report twice the building. These
    are marked excluded rather than deleted: excluded is ignored by the
    estimate and by the gate, the row keeps its history, and nothing a person
    reviewed disappears without a trace.
    """
    res = db.execute(_T(
        "UPDATE quantity_overrides SET status = 'excluded', "
        "notes = coalesce(notes, '') || :note "
        "WHERE project_id = :p AND source_document_id = :d "
        "AND source = 'cad_extract' AND status <> 'excluded'"),
        {"p": str(project_id), "d": str(dupe["id"]),
         "note": f" | superseded: same drawing as {dupe['duplicate_of']}."})
    return int(res.rowcount or 0)


def build_pack(db: Session, project_id: UUID) -> dict[str, Any]:
    """Read every CAD drawing on the project, write the quantities as candidates,
    and record the audit and accuracy alongside them."""
    from app.services.drawing import cad_extractor as cad
    from app.services.drawing.set_completeness import (
        Sheet, audit_set, level_names_from, sheet_number, sheet_type,
    )  # noqa: F401 — sheet_type is used for the measurable/not decision below
    from app.services.estimate_readiness import compute_readiness
    from app.services.r2 import get_bytes

    docs, dupes = _dedupe_formats(_cad_documents(db, project_id))
    sheets, per_sheet, read_ids = [], [], []
    for d in dupes:
        # Recorded, not silently dropped — a reviewer who uploaded both formats
        # should see why only one of them produced quantities.
        per_sheet.append({"document_id": str(d["id"]), "filename": d["filename"],
                          "building": _building(d["filename"] or ""),
                          "skipped": f"same drawing as {d['duplicate_of']}",
                          "superseded": _supersede(db, project_id, d)})
    for d in docs:
        suffix = os.path.splitext(d["filename"] or "")[1].lower() or ".dwg"
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                tf.write(get_bytes(d["r2_key"]))
                path = tf.name
            takeoff = cad.run_takeoff(path)
            role = sheet_type(d["filename"] or "")
            refuse = _NOT_MEASURABLE.get(role or "")
            if refuse:
                # Read for the set audit — which storeys the set covers is exactly
                # what sections are evidence of — but contribute no quantities,
                # and retire any an earlier build wrote from it.
                written = 0
                retired = _supersede(db, project_id,
                                     {**d, "duplicate_of": f"{role} sheet"})
            else:
                written = cad.takeoff_to_overrides(db, project_id, takeoff,
                                                   document_id=d["id"])
                retired = 0
            summary = cad.takeoff_summary(takeoff)
            per_sheet.append({
                "document_id": str(d["id"]), "filename": d["filename"],
                # Which building this sheet was grouped into. Roof and perimeter
                # resolve per building, and the grouping is read from the
                # filename, so a mis-parse has to be visible to the reviewer
                # rather than buried in an aggregate.
                "building": _building(d["filename"] or ""),
                "role": role,
                "not_measured": refuse,
                "superseded": retired,
                "quantities_written": written,
                "counts": summary.get("counts"), "sizes": summary.get("sizes"),
                "derived": summary.get("derived"), "warnings": summary.get("warnings"),
                "views": len(summary.get("views") or []),
            })
            read_ids.append(str(d["id"]))
            covered = sorted({s for s in (
                _storey(v.get("title")) for v in (takeoff.views or [])) if s})
            sheets.append(Sheet(d["filename"], _building(d["filename"]),
                                sheet_number(d["filename"]), sheet_type(d["filename"]),
                                storeys_covered=covered))
        except Exception as exc:  # noqa: BLE001 — one bad sheet must not lose the rest
            per_sheet.append({"document_id": str(d["id"]), "filename": d["filename"],
                              "error": f"{type(exc).__name__}: {exc}"[:200]})
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    db.commit()

    audit = audit_set(sheets) if sheets else None
    readiness = compute_readiness(db, project_id) or {}
    return {
        "sheets_read": len(read_ids),
        "sheets": per_sheet,
        "accuracy": readiness.get("accuracy"),
        "takeoff_mode": readiness.get("takeoff_mode"),
        "set_audit": {
            "complete": getattr(audit, "complete", None),
            "findings": getattr(audit, "findings", []),
            "by_building": getattr(audit, "by_building", {}),
        } if audit else None,
        "source_document_ids": read_ids,
    }


def _storey(title: str | None) -> str | None:
    from app.services.drawing.project_takeoff import classify_storey
    return classify_storey(title)


def _building(name: str) -> str:
    from app.services.drawing.project_takeoff import building_key
    return building_key(name)
