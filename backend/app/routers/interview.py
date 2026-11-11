"""The guided interview: what is outstanding, and what each answer buys.

Read-only. Answering a question happens on the surface that owns the record —
project type on the details page, quantities in the takeoff — because this
module reports what is missing and does not become a second way to write the
same fields.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user
from app.db import get_db
from app.services.interview import build_interview, outstanding_for_scope

router = APIRouter(prefix="/interview", tags=["interview"])


@router.get("")
def get_interview(
    project_id: UUID = Query(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    try:
        return build_interview(db, project_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/outstanding")
def outstanding(
    project_id: UUID = Query(...),
    answered: dict[str, Any] = Body(default={}),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """What would be carried into the scope's information-required list if the
    interview stopped here. Returns the lines; it does not write them."""
    assert_project_access(db, user, project_id)
    try:
        return {"information_required": outstanding_for_scope(db, project_id, answered)}
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
