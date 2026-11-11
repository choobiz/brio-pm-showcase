"""The gate between a machine's quantity takeoff and a priced estimate.

`takeoff_to_overrides` writes CAD-extracted quantities with the note "Reconcile vs
PDF & confirm before estimate". Nothing enforced it. `_verified_quantities`
selected every override row for the discipline, no filter on source or state, and
its own docstring described the result as "the human-verified ground truth from the
review UI" — so machine candidates reached the estimator wearing that label.

This module is the enforcement. An estimate cannot run while any quantity it would
consume is still a candidate, and the refusal names them.

**Confidence is stated up front, not as a footnote.** A measured takeoff needs CAD;
without it the estimate falls back to reading PDFs, which is a materially less
accurate path, and the person confirming the numbers has to know which one they are
looking at before they start clicking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.models.quantity_override import PRICEABLE_STATUSES

#: Job kinds that price quantities and therefore have to pass the gate.
GATED_JOB_KINDS = ("estimate_full", "estimate_generation")

ACCURACY: dict[str, str] = {
    "cad": ("MEASURED — quantities come from the CAD model: counts from block "
            "references, areas and lengths from line-work. This is the accurate path."),
    "pdf": ("LESS ACCURATE — no CAD in this project, so quantities are read from "
            "PDF drawings and rates. Expect to correct more of them; a DWG or DXF "
            "upload would replace this with a measured takeoff."),
    "none": ("LOWEST CONFIDENCE — no drawings at all. Quantities come from the "
             "scope and rate library only, and nothing here was measured."),
}


@dataclass
class GateStatus:
    open: bool                              # True = blocking, work outstanding
    takeoff_mode: str
    accuracy: str
    grade: Optional[dict[str, Any]] = None  # per-discipline accuracy detail
    counts: dict[str, int] = field(default_factory=dict)
    unresolved: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "open": self.open,
            "takeoff_mode": self.takeoff_mode,
            "accuracy": self.accuracy,
            "counts": self.counts,
            "grade": self.grade,
            "unresolved": self.unresolved,
        }


def gate_status(db: Session, project_id: UUID, discipline: Optional[str] = None,
                limit: int = 200) -> GateStatus:
    """Is the gate open, and what is holding it open?"""
    from app.services.estimate_readiness import compute_readiness

    grade = None
    try:
        r = compute_readiness(db, project_id) or {}
        mode = r.get("takeoff_mode") or "none"
        grade = r.get("accuracy")
    except Exception:  # noqa: BLE001 — readiness is advisory; never block on its failure
        mode = "none"

    params: dict[str, Any] = {"p": str(project_id)}
    disc = ""
    if discipline:
        disc = " AND discipline = :d"
        params["d"] = discipline
    rows = db.execute(_T(
        f"SELECT status, COUNT(*) AS n FROM quantity_overrides "
        f"WHERE project_id = :p{disc} GROUP BY status"), params).mappings().all()
    counts = {r["status"]: int(r["n"]) for r in rows}

    pending = db.execute(_T(
        f"SELECT id, discipline, quantity_type, value, unit, source, notes, "
        f"source_document_id "
        f"FROM quantity_overrides WHERE project_id = :p{disc} AND status = 'candidate' "
        f"ORDER BY discipline, quantity_type LIMIT {int(limit)}"), params).mappings().all()

    return GateStatus(
        open=bool(pending),
        takeoff_mode=mode,
        accuracy=(grade or {}).get("headline") or ACCURACY.get(mode, ACCURACY["none"]),
        grade=grade,
        counts=counts,
        unresolved=[{
            "id": str(r["id"]), "discipline": r["discipline"],
            "quantity_type": r["quantity_type"],
            "value": float(r["value"]) if r["value"] is not None else None,
            "unit": r["unit"], "source": r["source"], "notes": r["notes"],
            # Which drawing this came off, so the review can show it beside the
            # number. Confirming a count without seeing where it was taken from
            # is assent, not review.
            "source_document_id": (str(r["source_document_id"])
                                   if r["source_document_id"] else None),
        } for r in pending],
    )


def _assert_pack_approved(db: Session, project_id: UUID) -> None:
    """A project with CAD must have a SIGNED analysis, not merely confirmed rows.

    Confirming forty numbers one at a time and signing the analysis are different
    acts. Without this, an estimate could price a set whose missing storey or
    failed sheet nobody ever looked at, because each individual quantity that DID
    get read was ticked.
    """
    from fastapi import HTTPException, status as http

    row = db.execute(_T(
        "SELECT status FROM drawing_analysis_packs WHERE project_id = :p "
        "ORDER BY version_number DESC LIMIT 1"), {"p": str(project_id)}).first()
    # Must match _cad_documents exactly. If the gate and the builder disagree
    # about what counts as CAD, a project can either be gated on files nobody
    # analysed or analysed without ever being gated.
    from app.services.estimate_readiness import IS_CAD_SQL

    has_cad = db.execute(_T(
        "SELECT 1 FROM documents WHERE project_id = :p AND archived = false "
        "AND {cad} LIMIT 1".format(cad=IS_CAD_SQL)),
        {"p": str(project_id)}).first()
    if not has_cad:
        return                       # nothing to analyse; readiness reports the mode
    if row is None:
        raise HTTPException(status_code=http.HTTP_409_CONFLICT, detail={
            "error": "drawing_analysis_required",
            "message": ("This project has CAD drawings but they have not been "
                        "analysed. Run the drawing analysis and approve it "
                        "before generating an estimate.")})
    if row[0] != "approved":
        raise HTTPException(status_code=http.HTTP_409_CONFLICT, detail={
            "error": "drawing_analysis_not_approved",
            "message": (f"The drawing analysis is {row[0]} — it has not been "
                        f"signed off. Approve it before generating an estimate.")})


def assert_quantities_reviewed(db: Session, project_id: UUID,
                               discipline: Optional[str] = None) -> None:
    """Refuse while any quantity is still a candidate — the first half of the gate.

    Separate from `assert_gate_closed` because APPROVING the analysis is exactly
    the act that satisfies the second half. Calling the whole gate before signing
    made approval require an already-approved pack, so the button refused itself
    with "the drawing analysis is draft — approve it first".
    """
    from fastapi import HTTPException, status as http

    g = gate_status(db, project_id, discipline)
    if not g.open:
        return
    n = len(g.unresolved)
    raise HTTPException(
        status_code=http.HTTP_409_CONFLICT,
        detail={
            "error": "quantity_verification_required",
            "message": (
                f"{n} quantit{'y' if n == 1 else 'ies'} from the drawing takeoff "
                f"have not been reviewed. Confirm, correct or exclude each one "
                f"before generating an estimate."),
            **g.as_dict(),
        },
    )


def assert_gate_closed(db: Session, project_id: UUID,
                       discipline: Optional[str] = None) -> None:
    """Refuse to price unless the quantities are reviewed AND the analysis signed.

    Both halves, in the order a person meets them. Raises 409 with the
    outstanding list, because "blocked" without "on what" just moves the work to
    whoever has to go and find out.
    """
    assert_quantities_reviewed(db, project_id, discipline)
    _assert_pack_approved(db, project_id)
