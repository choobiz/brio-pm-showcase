"""Contract › Billing plan: when each signed stage is billed; saving builds the
schedule of values and the cash flow.

Office only (contract money). See services/billing_plan.py for the rules.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office
from app.db import get_db
from app.services import billing_plan as svc

router = APIRouter(prefix="/projects/{project_id}/billing-plan", tags=["billing-plan"])


class PlanIn(BaseModel):
    stage_activities: dict[str, Optional[str]] = {}
    division_phases: dict[str, Optional[str]] = {}
    cost_share_pct: Optional[float] = None
    owner_net_days: Optional[int] = None
    substantial_completion_actual: Optional[date] = None


def _uid(user: CurrentUser) -> UUID | None:
    try:
        return UUID(str(user.id))
    except (TypeError, ValueError):
        return None


def _body(body: PlanIn) -> dict:
    # Only send substantial_completion_actual through when it was given, so
    # leaving it out keeps what's recorded; null in the body clears it.
    return body.model_dump(exclude_unset=True)


def _fail(e: svc.PlanError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


@router.get("")
def get_plan(project_id: UUID, db: Session = Depends(get_db),
             user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    try:
        return svc.public(svc.view(db, project_id))
    except svc.PlanError as e:
        raise _fail(e) from e


@router.post("/preview")
def preview_plan(project_id: UUID, body: PlanIn, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """The plan with unsaved picks applied. Writes nothing."""
    assert_project_access(db, user, project_id)
    try:
        return svc.public(svc.view(db, project_id, _body(body)))
    except svc.PlanError as e:
        raise _fail(e) from e


@router.put("")
def save_plan(project_id: UUID, body: PlanIn, db: Session = Depends(get_db),
              user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    try:
        return svc.save(db, project_id, _body(body), _uid(user))
    except svc.PlanError as e:  # refused before anything is written
        raise _fail(e) from e


@router.get("/progress")
def sov_progress(project_id: UUID, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """Schedule of values with % done / earned / billed / left, and the cash
    flow by month (R2Billing). Null when nothing has been built yet."""
    assert_project_access(db, user, project_id)
    from app.services.sov_progress import progress
    return {"progress": progress(db, project_id)}


@router.get("/sov.pdf")
def sov_pdf(project_id: UUID, db: Session = Depends(get_db),
            user: CurrentUser = Depends(require_office)) -> Response:
    assert_project_access(db, user, project_id)
    from app.services.sov_progress import render_pdf
    try:
        pdf = render_pdf(db, project_id)
    except svc.PlanError as e:
        raise _fail(e) from e
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="schedule-of-values.pdf"'})
