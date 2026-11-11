"""Client › Proposal (office side): send a presentation to the client portal,
withdraw it, and see whether it was accepted. Office only.
See services/proposal_portal.py.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office
from app.db import get_db
from app.services import proposal_portal as svc

router = APIRouter(prefix="/projects/{project_id}/proposals", tags=["proposals-portal"])


def _uid(user: CurrentUser) -> UUID | None:
    try:
        return UUID(str(user.id))
    except (TypeError, ValueError):
        return None


@router.get("")
def proposal_status(project_id: UUID, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require_office)) -> list[dict[str, Any]]:
    assert_project_access(db, user, project_id)
    return svc.office_status(db, project_id)


@router.post("/{presentation_id}/send")
def send_to_portal(project_id: UUID, presentation_id: UUID, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_office)) -> list[dict[str, Any]]:
    assert_project_access(db, user, project_id)
    try:
        svc.send(db, project_id, presentation_id, _uid(user))
    except svc.ProposalError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    db.commit()
    from app.services.notify import notify_owner_message
    notify_owner_message(db, project_id, preview="Your proposal is ready to review in your portal.")  # best-effort
    return svc.office_status(db, project_id)


@router.post("/{presentation_id}/withdraw")
def withdraw_from_portal(project_id: UUID, presentation_id: UUID, db: Session = Depends(get_db),
                         user: CurrentUser = Depends(require_office)) -> list[dict[str, Any]]:
    assert_project_access(db, user, project_id)
    try:
        svc.withdraw(db, project_id, presentation_id, _uid(user))
    except svc.ProposalError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    db.commit()
    return svc.office_status(db, project_id)


class TextIn(BaseModel):
    headline: str | None = None
    overview: str | None = None
    closingNote: str | None = None


@router.patch("/{presentation_id}/text")
def edit_proposal_text(project_id: UUID, presentation_id: UUID, body: TextIn, db: Session = Depends(get_db),
                       user: CurrentUser = Depends(require_office)) -> list[dict[str, Any]]:
    assert_project_access(db, user, project_id)
    try:
        svc.edit_text(db, project_id, presentation_id, body.model_dump(exclude_unset=True), _uid(user))
    except svc.ProposalError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    db.commit()
    return svc.office_status(db, project_id)
