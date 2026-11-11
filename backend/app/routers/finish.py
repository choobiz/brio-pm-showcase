"""The finish, asked and confirmed — see services/finish_spec."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_office, require_project_member
from app.db import get_db
from app.services import finish_spec as F

router = APIRouter(prefix="/projects/{project_id}/finish", tags=["finish"],
                   dependencies=[Depends(require_project_member)])

DELIVERED = ("finished", "shell", "part_shell")


@router.get("")
def read_finish(project_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    out = F.finish_state(db, project_id)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="project not found")
    return out


class DeliveryBody(BaseModel):
    delivered_as: str | None = None
    part_shell_note: str | None = Field(None, max_length=2000)


@router.patch("")
def set_delivery(project_id: UUID, body: DeliveryBody, db: Session = Depends(get_db),
                 _: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """Delivered as, and for a part shell which areas are left unfinished.
    Fields not sent are left alone."""
    from app.models.project import Project

    p = db.get(Project, project_id)
    sent = body.model_fields_set
    if "delivered_as" in sent:
        if body.delivered_as is not None and body.delivered_as not in DELIVERED:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"delivered_as must be one of {', '.join(DELIVERED)}")
        p.delivered_as = body.delivered_as
    if "part_shell_note" in sent:
        p.part_shell_note = (body.part_shell_note or "").strip() or None
    db.commit()
    return F.finish_state(db, project_id)


class AnswerBody(BaseModel):
    answer: str | None = Field(None, max_length=500)
    confirmed: bool = False


@router.put("/{key}")
def answer_question(project_id: UUID, key: str, body: AnswerBody,
                    db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """Answer one finish question. `confirmed` marks it as decided by a person;
    only confirmed answers reach the estimate as fact."""
    try:
        F.set_answer(db, project_id, key, body.answer, body.confirmed, user.id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such finish question")
    db.commit()
    return F.finish_state(db, project_id)


@router.post("/suggest")
def suggest_answers(project_id: UUID, db: Session = Depends(get_db),
                    _: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """Propose an answer for each unconfirmed finish, read from the latest scope.
    Nothing is confirmed by this; an answer already typed is kept."""
    state = F.finish_state(db, project_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="project not found")
    if not state["asks_finishes"]:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Set delivered-as to finished or part shell first; a shell has no finishes.")
    try:
        F.suggest(db, project_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(e))
    db.commit()
    return F.finish_state(db, project_id)
