"""Schedule › Update the plan: a new version from what actually happened.

GET previews (nothing written); POST saves the next version. Office only. See
services/replan.py for the rules and services/schedule_replan.py for the save.
"""
from __future__ import annotations

from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office
from app.db import get_db
from app.services import schedule_replan as svc

router = APIRouter(prefix="/projects/{project_id}/schedule/replan", tags=["schedule"])

Rule = Literal["keep", "push"]


class ReplanIn(BaseModel):
    base_schedule_id: UUID
    unanswered: Rule = "keep"
    client_visible: bool = False
    note: Optional[str] = None


def _uid(user: CurrentUser) -> UUID | None:
    try:
        return UUID(str(user.id))
    except (TypeError, ValueError):
        return None


@router.get("")
def preview_replan(
    project_id: UUID,
    unanswered: Rule = Query("keep"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    try:
        return svc.preview(db, project_id, unanswered)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e


@router.post("", status_code=status.HTTP_201_CREATED)
def save_replan(
    project_id: UUID,
    body: ReplanIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    try:
        out = svc.save(db, project_id, base_schedule_id=body.base_schedule_id, unanswered=body.unanswered,
                       client_visible=body.client_visible, note=body.note, user_id=_uid(user))
    except svc.StaleBase as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    db.commit()
    return out
