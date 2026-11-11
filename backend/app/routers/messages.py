"""Project messages — office side (manager/superadmin).

A single client↔office thread per project. The client uses the owner portal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office, real_user_id
from app.db import get_db
from app.models.project_message import ProjectMessage


router = APIRouter(prefix="/projects/{project_id}/messages", tags=["messages"])


class MessageCreate(BaseModel):
    body: str


def msg_read(m: ProjectMessage) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "sender_role": m.sender_role,
        "body": m.body,
        "read_at": m.read_at.isoformat() if m.read_at else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def mark_read(db: Session, project_id: UUID, reader_role: str) -> None:
    """Mark the counterpart's messages read when this side opens the thread."""
    other = "owner" if reader_role == "office" else "office"
    db.execute(
        text("UPDATE project_messages SET read_at = now() "
             "WHERE project_id = :p AND sender_role = :o AND read_at IS NULL"),
        {"p": project_id, "o": other},
    )
    db.commit()


def _uid(user: CurrentUser) -> UUID | None:
    return real_user_id(user)


@router.get("")
def list_messages(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    assert_project_access(db, user, project_id)
    mark_read(db, project_id, "office")
    rows = db.execute(
        select(ProjectMessage).where(ProjectMessage.project_id == project_id)
        .order_by(ProjectMessage.created_at)
    ).scalars().all()
    return {"messages": [msg_read(m) for m in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
def send_message(
    project_id: UUID,
    body: MessageCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    assert_project_access(db, user, project_id)
    text_body = (body.body or "").strip()
    if not text_body:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="message body is required")
    m = ProjectMessage(project_id=project_id, sender_id=_uid(user), sender_role="office", body=text_body)
    db.add(m)
    db.commit()
    db.refresh(m)
    from app.services.notify import notify_owner_message
    notify_owner_message(db, project_id, preview=text_body[:140])  # best-effort
    return msg_read(m)


@router.get("/unread")
def unread_count(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Count of client (owner) messages the office hasn't read yet — drives the
    in-project unread badge. Does NOT mark anything read."""
    assert_project_access(db, user, project_id)
    n = db.execute(
        text("SELECT COUNT(*) FROM project_messages "
             "WHERE project_id = :p AND sender_role = 'owner' AND read_at IS NULL"),
        {"p": project_id},
    ).scalar()
    return {"count": int(n or 0)}
