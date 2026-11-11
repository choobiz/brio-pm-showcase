"""On-site QA walkthroughs.

⚠️ NOT authority inspections (see `inspections`), and ⚠️ not a code-compliance
instrument — every response carries the disclaimer for that reason.

Recording an observation is a FIELD-level act, not an office one: the person
holding the phone on site is the one who looked. Completing is the same act
finished, so it is gated the same way.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user
from app.db import get_db
from app.services import qa_checklists as svc

router = APIRouter(prefix="/qa-checklists", tags=["qa-checklists"])


class CreateRequest(BaseModel):
    project_id: UUID
    template_key: str
    location: str | None = None


class ResultRequest(BaseModel):
    item_id: str
    #: None puts an item back to not-yet-looked-at. "pass" | "fail" | "na".
    result: str | None = None
    note: str | None = None
    photos: list[str] | None = None


class CompleteRequest(BaseModel):
    #: Must be asked for. A partial walk is legitimate; closing one silently is
    #: how "we did not look" becomes "we looked and it was fine".
    allow_unanswered: bool = False


@router.get("/templates")
def templates(_: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return {"templates": svc.list_templates(), "disclaimer": svc.DISCLAIMER}


@router.get("")
def list_checklists(
    project_id: UUID = Query(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    return {"checklists": svc.list_for_project(db, project_id),
            "disclaimer": svc.DISCLAIMER}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_checklist(
    body: CreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    assert_project_access(db, user, body.project_id)
    try:
        out = svc.create(db, project_id=body.project_id,
                         template_key=body.template_key, location=body.location,
                         performed_by_id=getattr(user, "id", None))
    except svc.ChecklistError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    db.commit()
    return out


def _owned(db: Session, user: CurrentUser, checklist_id: UUID) -> dict[str, Any]:
    try:
        cl = svc.get(db, checklist_id)
    except svc.ChecklistError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    assert_project_access(db, user, UUID(cl["project_id"]))
    return cl


@router.get("/{checklist_id}")
def get_checklist(
    checklist_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    return _owned(db, user, checklist_id)


@router.post("/{checklist_id}/result")
def record_result(
    checklist_id: UUID,
    body: ResultRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _owned(db, user, checklist_id)
    try:
        out = svc.set_result(db, checklist_id, body.item_id, result=body.result,
                             note=body.note, photos=body.photos)
    except svc.ChecklistError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e))
    db.commit()
    return out


@router.post("/{checklist_id}/complete")
def complete_checklist(
    checklist_id: UUID,
    body: CompleteRequest = Body(default=CompleteRequest()),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    _owned(db, user, checklist_id)
    try:
        out = svc.complete(db, checklist_id, user_id=getattr(user, "id", None),
                           allow_unanswered=body.allow_unanswered)
    except svc.ChecklistError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e))
    db.commit()
    return out
