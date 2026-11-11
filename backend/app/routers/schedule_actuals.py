"""Actual progress per schedule activity (Schedule › Record what happened).

GET merges what the daily site reports say with what was typed in; PUT records
typed-in facts for one activity. See services/actuals.py for the rules.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_field, require_office
from app.db import get_db
from app.services import actuals as svc

router = APIRouter(prefix="/projects/{project_id}/schedule/actuals", tags=["schedule"])


class ActualIn(BaseModel):
    status: Optional[Literal["done", "started", "not_started"]] = None
    actual_start: Optional[date] = None
    actual_finish: Optional[date] = None
    percent_complete: Optional[int] = Field(default=None, ge=0, le=100)
    reason: Optional[str] = None


def _uid(user: CurrentUser) -> UUID | None:
    try:
        return UUID(str(user.id))
    except (TypeError, ValueError):
        return None


@router.get("")
def list_actuals(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_field),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    rows = svc.project_actuals(db, project_id)
    return {"actuals": sorted(rows.values(), key=lambda r: r["activity_code"])}


@router.put("/{activity_code}")
def record_actual(
    project_id: UUID,
    activity_code: str,
    body: ActualIn,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    try:
        svc.record(db, project_id, activity_code, body.model_dump(exclude_unset=True), _uid(user))
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    db.commit()
    return svc.project_actuals(db, project_id)[activity_code]
