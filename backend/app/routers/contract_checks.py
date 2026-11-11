"""Contract › checks, time extension, countersigned copy, amend (R2Contract).
Office only. See services/contract_checks.py."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, real_user_id, require_office
from app.db import get_db
from app.services import contract_checks as svc

router = APIRouter(prefix="/projects/{project_id}/contract", tags=["contract-checks"])


class SignedCopyIn(BaseModel):
    document_id: UUID


def _fail(e: svc.ContractError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))


@router.get("/checks")
def contract_checks(project_id: UUID, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    return svc.checks(db, project_id)


@router.post("/time-extension")
def time_extension(project_id: UUID, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """Drafts a no-cost change order for the days the schedule has moved past
    the contract date. Nothing is sent."""
    assert_project_access(db, user, project_id)
    try:
        co = svc.draft_time_extension(db, project_id, real_user_id(user))
    except svc.ContractError as e:
        raise _fail(e) from e
    db.commit()
    return {"id": str(co.id), "number": co.number, "schedule_impact_days": co.schedule_impact_days}


@router.put("/signed-copy")
def signed_copy(project_id: UUID, body: SignedCopyIn, db: Session = Depends(get_db),
                user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    try:
        svc.set_signed_copy(db, project_id, body.document_id)
    except svc.ContractError as e:
        raise _fail(e) from e
    db.commit()
    return svc.checks(db, project_id)


@router.post("/amend")
def amend(project_id: UUID, db: Session = Depends(get_db),
          user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    try:
        out = svc.amend(db, project_id, real_user_id(user))
    except svc.ContractError as e:
        raise _fail(e) from e
    db.commit()
    return out
