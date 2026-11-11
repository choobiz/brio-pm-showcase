"""Client › Conversation: the portal thread and logged emails/calls/notes as
one timeline. Office only. Opening it marks the client's portal messages read,
as opening Messages did. See services/conversation.py.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office
from app.db import get_db
from app.routers.messages import mark_read
from app.services import conversation as svc

router = APIRouter(prefix="/projects/{project_id}/conversation", tags=["conversation"])


@router.get("")
def get_conversation(project_id: UUID, db: Session = Depends(get_db),
                     user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    out = svc.timeline(db, project_id)
    mark_read(db, project_id, "office")
    return out


@router.get("/schedule-reply")
def schedule_reply(project_id: UUID, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """A reply draft from the schedule the client sees. Writes nothing."""
    assert_project_access(db, user, project_id)
    return {"text": svc.schedule_reply(db, project_id)}
