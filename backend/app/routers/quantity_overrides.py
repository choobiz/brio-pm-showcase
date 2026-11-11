"""Quantity-override API — surface for the review UI."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.auth import (
    CurrentUser,
    assert_project_access,
    get_current_user,
    require_office,
)
from app.db import get_db
from app.models.quantity_override import QuantityOverride
from app.services.quantity_source import SourceRejected, stamp as stamp_source


router = APIRouter(prefix="/quantity-overrides", tags=["quantity-overrides"])


class OverrideWrite(BaseModel):
    project_id: UUID
    discipline: str
    quantity_type: str
    value: float | None = None
    unit: str
    source: str  # 'candidate_confirmed' | 'manual_measure' | 'manual_entry'
    source_document_id: UUID | None = None
    source_page: int | None = None
    source_candidate_key: str | None = None
    notes: str | None = None
    #: [x0, y0, x1, y1] in PDF user-space units, pre-rotation. PDF path only —
    #: see the column comment on QuantityOverride for why CAD gets none.
    source_bbox: list[float] | None = None
    source_revision_label: str | None = None


class OverrideRead(BaseModel):
    id: UUID
    project_id: UUID
    discipline: str
    quantity_type: str
    value: float | None
    unit: str
    source: str
    source_document_id: UUID | None
    source_page: int | None
    source_candidate_key: str | None
    notes: str | None
    status: str
    source_bbox: list[float] | None = None
    source_revision_label: str | None = None
    machine_value: float | None
    confirmed_by_id: UUID | None
    confirmed_at: datetime | None

    @classmethod
    def from_orm(cls, row: QuantityOverride) -> "OverrideRead":
        return cls(
            id=row.id,
            project_id=row.project_id,
            discipline=row.discipline,
            quantity_type=row.quantity_type,
            value=float(row.value) if row.value is not None else None,
            unit=row.unit,
            source=row.source,
            source_document_id=row.source_document_id,
            source_page=row.source_page,
            source_candidate_key=row.source_candidate_key,
            notes=row.notes,
            status=row.status,
            source_bbox=row.source_bbox,
            source_revision_label=row.source_revision_label,
            machine_value=(float(row.machine_value)
                           if row.machine_value is not None else None),
            confirmed_by_id=row.confirmed_by_id,
            confirmed_at=row.confirmed_at,
        )


@router.get("", response_model=list[OverrideRead])
def list_overrides(
    project_id: UUID = Query(...),
    discipline: str | None = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[OverrideRead]:
    assert_project_access(db, user, project_id)
    stmt = select(QuantityOverride).where(QuantityOverride.project_id == project_id)
    if discipline:
        stmt = stmt.where(QuantityOverride.discipline == discipline)
    rows = db.execute(stmt).scalars().all()
    return [OverrideRead.from_orm(r) for r in rows]


@router.post("", response_model=OverrideRead, status_code=status.HTTP_201_CREATED)
def upsert_override(
    body: OverrideWrite,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> OverrideRead:
    """Upsert by (source_document_id, source_candidate_key) when both are
    set — clicking ✓ twice on the same candidate updates the row instead of
    duplicating. Manual measures and entries always insert new rows."""
    assert_project_access(db, user, body.project_id)
    existing = None
    if body.source_document_id and body.source_candidate_key:
        existing = (
            db.query(QuantityOverride)
            .filter_by(
                source_document_id=body.source_document_id,
                source_candidate_key=body.source_candidate_key,
            )
            .first()
        )
    payload = body.model_dump()
    if body.value is not None:
        payload["value"] = Decimal(str(body.value))
    # Drawing provenance is resolved from the record, never taken from the body:
    # a revision label the caller supplies would look checkable without being so.
    # See app/services/quantity_source.py for why a bbox is PDF-only.
    try:
        payload = stamp_source(db, payload, body.project_id)
    except SourceRejected as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    if existing is not None:
        for k, v in payload.items():
            setattr(existing, k, v)
        db.commit()
        db.refresh(existing)
        return OverrideRead.from_orm(existing)
    row = QuantityOverride(**payload)
    db.add(row)
    db.commit()
    db.refresh(row)
    return OverrideRead.from_orm(row)


@router.delete("/{override_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_override(
    override_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    row = db.get(QuantityOverride, override_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    # Resolved from the ROW, not the request — the caller supplies only an
    # override id, so the project has to be looked up before it can be checked.
    assert_project_access(db, user, row.project_id)
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── verification gate ────────────────────────────────────────────────────────
# A quantity a machine produced is a candidate until a person says otherwise.
# Confirming is restricted to office roles (manager and above) because it is the
# act that lets a number reach a client price. BRIO has no separate `estimator`
# role; `require_office` is manager + superadmin, which is that group.


class ReviewWrite(BaseModel):
    action: str                      # 'confirm' | 'correct' | 'exclude'
    value: float | None = None       # required for 'correct'
    notes: str | None = None


class BulkReviewWrite(BaseModel):
    project_id: UUID
    ids: list[UUID]
    action: str                      # 'confirm' | 'exclude' — a correction is one value, one row
    notes: str | None = None


@router.post("/review-bulk", response_model=dict)
def review_overrides_bulk(
    body: BulkReviewWrite,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Confirm or exclude several quantities at once — the same kind of mistake
    across many rows ("every one of these is existing work").

    All or nothing: an id from another project refuses the whole request rather
    than quietly changing the rest. Correction is deliberately not offered here;
    a corrected value belongs to one measurement.
    """
    assert_project_access(db, user, body.project_id)
    action = (body.action or "").lower()
    if action not in ("confirm", "exclude"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="action must be confirm or exclude")
    ids = list(dict.fromkeys(body.ids))
    if not ids or len(ids) > 500:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="send between 1 and 500 quantities")
    rows = db.execute(select(QuantityOverride).where(QuantityOverride.id.in_(ids))).scalars().all()
    if len(rows) != len(ids) or any(r.project_id != body.project_id for r in rows):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="one or more quantities are not on this project")
    now = datetime.now(timezone.utc)
    new_status = "confirmed" if action == "confirm" else "excluded"
    for row in rows:
        if row.machine_value is None:
            row.machine_value = row.value
        row.status = new_status
        if body.notes:
            row.notes = ((row.notes + " | ") if row.notes else "") + body.notes
        row.confirmed_by_id = UUID(user.id) if user.id else None
        row.confirmed_at = now
    db.commit()
    return {"updated": len(rows), "status": new_status}


@router.get("/gate", response_model=dict)
def read_gate(
    project_id: UUID = Query(...),
    discipline: str | None = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Is this project's takeoff reviewed, and how accurate is the source?

    `accuracy` states plainly whether the quantities were MEASURED from CAD or
    read from PDFs. Surface it at the top of the review screen — someone
    confirming a hundred numbers needs to know which path produced them before
    they start, not after.
    """
    from app.services.quantity_gate import gate_status

    assert_project_access(db, user, project_id)
    return gate_status(db, project_id, discipline).as_dict()


@router.get("/{override_id}/provenance", response_model=dict)
def read_provenance(
    override_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Where this number came from — and where the trail stops.

    Read-only. Declared before nothing in particular, but note the sibling
    "/gate" route above is static and must stay ahead of any bare
    "/{override_id}" GET.

    Every unrecorded link is returned as the string "not recorded" rather than
    zero, null or an empty list. Presenting an unmeasured link as a measured
    absence is the failure this endpoint is shaped to avoid.
    """
    from app.services.provenance import quantity_provenance

    row = db.execute(
        _T("SELECT project_id FROM quantity_overrides WHERE id = :i"),
        {"i": str(override_id)},
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="quantity not found")
    assert_project_access(db, user, row[0])

    out = quantity_provenance(db, override_id)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="quantity not found")
    return out


@router.post("/{override_id}/review", response_model=OverrideRead)
def review_override(
    override_id: UUID,
    body: ReviewWrite,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> OverrideRead:
    """Confirm, correct or exclude one quantity.

    A correction keeps `machine_value` so the gap between what the drawing was
    read to say and what a person decided stays visible — that difference is the
    only measure of how well the extractor is doing on real projects.
    """
    row = db.get(QuantityOverride, override_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    # require_office establishes the ROLE; this establishes access to THIS
    # project. Being a manager is not the same as being on this job.
    assert_project_access(db, user, row.project_id)
    action = (body.action or "").lower()
    if action not in ("confirm", "correct", "exclude"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="action must be confirm, correct or exclude")
    if action == "correct" and body.value is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="'correct' requires a value")

    if row.machine_value is None:
        row.machine_value = row.value
    if action == "correct":
        row.value = Decimal(str(body.value))
        row.status = "corrected"
    else:
        row.status = "confirmed" if action == "confirm" else "excluded"
    if body.notes:
        row.notes = ((row.notes + " | ") if row.notes else "") + body.notes
    row.confirmed_by_id = UUID(user.id) if user.id else None
    row.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return OverrideRead.from_orm(row)
