"""Estimates router — list / get / patch / PDF.

Estimates are created by the AI worker when an `estimate_generation` AIJob
succeeds. This router exposes the review-and-edit surface plus PDF generation.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user, require_office
from app.db import get_db
from app.models.client import Client
from app.models.document import Document
from app.models.estimate import Estimate
from app.services.revisions import actor_uuid, snapshot
from app.models.project import Project
from app.schemas.estimate import EstimatePatch, EstimatePdfResult, EstimateRead
from app.services.estimate_pdf import render_estimate_pdf
from app.services.r2 import presign_get, put_bytes

# Office only (rollout part 3, 2026-10-15): signed in as the test CREW account,
# these answered with money (contract, owing, estimate costs and margins,
# invoices, rates). The field pages never call them.
router = APIRouter(prefix="/estimates", tags=["estimates"], dependencies=[Depends(require_office)])


@router.get("", response_model=list[EstimateRead])
def list_estimates(
    project_id: UUID = Query(..., description="filter to estimates for this project"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[Estimate]:
    # None of this router checked the project until 2026-10-12.
    assert_project_access(db, user, project_id)
    stmt = (
        select(Estimate)
        .where(Estimate.project_id == project_id)
        .order_by(Estimate.version.desc())
    )
    return list(db.execute(stmt).scalars().all())


@router.get("/{estimate_id}", response_model=EstimateRead)
def get_estimate(
    estimate_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Estimate:
    estimate = db.get(Estimate, estimate_id)
    if estimate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="estimate not found")
    assert_project_access(db, user, estimate.project_id)
    return estimate


@router.patch("/{estimate_id}", response_model=EstimateRead)
def update_estimate(
    estimate_id: UUID,
    body: EstimatePatch,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> Estimate:
    estimate = db.get(Estimate, estimate_id)
    if estimate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="estimate not found")
    assert_project_access(db, user, estimate.project_id)
    snapshot(
        db, table="estimates", row_id=estimate.id, project_id=estimate.project_id,
        action="edit", actor_id=actor_uuid(user),
    )
    estimate.payload = body.payload
    if body.status is not None:
        estimate.status = body.status
    db.commit()
    db.refresh(estimate)
    return estimate


@router.post("/{estimate_id}/pdf", response_model=EstimatePdfResult)
def generate_estimate_pdf(
    estimate_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> EstimatePdfResult:
    """Render the estimate's current payload to a branded PDF, store it in R2
    as an `estimate_pdf` Document, and return a short-lived download URL."""
    estimate = db.get(Estimate, estimate_id)
    if estimate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="estimate not found")
    assert_project_access(db, user, estimate.project_id)

    project = db.get(Project, estimate.project_id)
    client = (
        db.get(Client, project.client_id)
        if project is not None and project.client_id is not None
        else None
    )
    project_dict = {
        "name": getattr(project, "name", None),
        "address": getattr(project, "address", None),
        "type": getattr(project, "type", None),
    }
    client_dict = {
        "name": getattr(client, "name", None),
        "email": getattr(client, "email", None),
        "phone": getattr(client, "phone", None),
        "address": getattr(client, "address", None),
    }

    pdf_bytes = render_estimate_pdf(
        payload=estimate.payload or {}, project=project_dict, client=client_dict
    )

    key = f"estimates/{estimate_id}/estimate-v{estimate.version}.pdf"
    put_bytes(key, pdf_bytes, "application/pdf")

    filename = f"Estimate - {project_dict['name'] or 'Project'} v{estimate.version}.pdf"
    doc = Document(
        project_id=estimate.project_id,
        category="estimate_pdf",
        phase="preconstruction",
        filename=filename,
        r2_key=key,
        mime="application/pdf",
        size_bytes=len(pdf_bytes),
        uploaded_by_id=UUID(user.id) if getattr(user, "id", None) else None,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return EstimatePdfResult(
        document_id=doc.id,
        filename=filename,
        size_bytes=len(pdf_bytes),
        download_url=presign_get(key),
    )
