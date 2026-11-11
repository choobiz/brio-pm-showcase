"""Drawing analysis API.

Surfaces the cached PyMuPDF parse result for any Document, so the review UI
can display linear candidates / scales / dimensions without ever shipping
raw 13 MB PDFs over the wire to the browser.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user
from app.db import get_db
from app.models.document import Document
from app.models.page_scale_override import PageScaleOverride
from app.services.drawing.analysis_queue import latest_job, queue_analysis
from app.services.drawing.cache import cached_analysis
from app.services.drawing.scale_detector import units_per_foot_from_label


router = APIRouter(prefix="/drawings", tags=["drawings"])


def _project_of(db: Session, document_id: UUID) -> UUID:
    """The project a document belongs to.

    Every endpoint in this router is keyed on a document id, so access cannot be
    checked until the project has been looked up. Raises 404 for a document that
    does not exist, which is also the right answer for one the caller may not
    see — a 403 would confirm it exists.
    """
    row = db.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return row.project_id


@router.get("/{document_id}/analysis")
def get_drawing_analysis(
    document_id: UUID,
    response: Response,
    force: bool = Query(False, description="re-parse even if a current analysis is stored"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """The stored PyMuPDF analysis for one drawing: pages, linear candidates,
    scales, dimensions. Used by the measure-on-PDF page.

    Never parses here. When nothing current is stored (or `force`), the parse
    is queued for the background worker and this answers 202 with
    `analysis_status` "analysing"; the page polls. A failed parse answers 202
    with "failed" until someone asks again with `force`. See
    services/drawing/analysis_queue.py for why.
    """
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    assert_project_access(db, user, doc.project_id)

    job = latest_job(db, document_id)
    pending = job is not None and job.status in ("queued", "running")
    if not force and not pending:
        result = cached_analysis(db, document_id)
        if result is not None:
            return {
                "id": str(result.id),
                "document_id": str(result.document_id),
                "content_hash": result.content_hash,
                "is_vector": result.is_vector,
                "page_count": result.page_count,
                "created_at": result.created_at.isoformat(),
                "updated_at": result.updated_at.isoformat(),
                **(result.payload or {}),
            }
        if job is not None and job.status == "error":
            response.status_code = status.HTTP_202_ACCEPTED
            return {"analysis_status": "failed", "job_id": str(job.id),
                    "error": job.error or "the drawing could not be analysed"}

    if not pending:
        job = queue_analysis(db, doc, force=force)
        db.commit()
    response.status_code = status.HTTP_202_ACCEPTED
    return {"analysis_status": "analysing", "job_id": str(job.id)}


# ── manual / calibrated per-page scale overrides ─────────────────────
# Auto-detection (scale_detector) fails when the title-block scale is
# vectorized text or in an uncovered notation. These let a human pin the
# scale so the measured takeoff can compute real feet.


class ScaleOverrideWrite(BaseModel):
    # Provide EITHER a named scale label (server derives the conversion) OR a
    # pre-computed pdf_units_per_real_foot (two-point calibration).
    scale_label: str | None = None
    pdf_units_per_real_foot: float | None = None
    source: str  # 'manual_select' | 'calibrated'


class ScaleOverrideRead(BaseModel):
    id: UUID
    document_id: UUID
    page: int
    pdf_units_per_real_foot: float
    scale_label: str | None
    source: str

    @classmethod
    def from_orm(cls, row: PageScaleOverride) -> "ScaleOverrideRead":
        return cls(
            id=row.id,
            document_id=row.document_id,
            page=row.page,
            pdf_units_per_real_foot=float(row.pdf_units_per_real_foot),
            scale_label=row.scale_label,
            source=row.source,
        )


@router.get("/{document_id}/scale-overrides", response_model=list[ScaleOverrideRead])
def list_scale_overrides(
    document_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[ScaleOverrideRead]:
    assert_project_access(db, user, _project_of(db, document_id))
    rows = (
        db.query(PageScaleOverride)
        .filter_by(document_id=document_id)
        .order_by(PageScaleOverride.page)
        .all()
    )
    return [ScaleOverrideRead.from_orm(r) for r in rows]


@router.put("/{document_id}/scale-overrides/{page}", response_model=ScaleOverrideRead)
def upsert_scale_override(
    document_id: UUID,
    page: int,
    body: ScaleOverrideWrite,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ScaleOverrideRead:
    """Pin a page's scale. ``pdf_units_per_real_foot`` wins when supplied
    (calibration); otherwise it's derived from ``scale_label``."""
    assert_project_access(db, user, _project_of(db, document_id))
    if db.get(Document, document_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")

    ppf = body.pdf_units_per_real_foot
    if ppf is None and body.scale_label:
        ppf = units_per_foot_from_label(body.scale_label)
    if ppf is None or ppf <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "could not resolve a positive scale from scale_label / "
            "pdf_units_per_real_foot",
        )

    existing = (
        db.query(PageScaleOverride)
        .filter_by(document_id=document_id, page=page)
        .first()
    )
    if existing is not None:
        existing.pdf_units_per_real_foot = Decimal(str(ppf))
        existing.scale_label = body.scale_label
        existing.source = body.source
        row = existing
    else:
        row = PageScaleOverride(
            document_id=document_id,
            page=page,
            pdf_units_per_real_foot=Decimal(str(ppf)),
            scale_label=body.scale_label,
            source=body.source,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return ScaleOverrideRead.from_orm(row)


@router.delete(
    "/{document_id}/scale-overrides/{page}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_scale_override(
    document_id: UUID,
    page: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Response:
    assert_project_access(db, user, _project_of(db, document_id))
    row = (
        db.query(PageScaleOverride)
        .filter_by(document_id=document_id, page=page)
        .first()
    )
    if row is not None:
        db.delete(row)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
