"""Project communications log (Conversations) — office side.

Manual capture now (paste/upload); BCC/forward email ingestion is a later phase.
Each logged communication is AI-classified into a construction-taxonomy category
with a summary, action items, and urgency.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office, real_user_id
from app.config import settings
from app.db import get_db
from app.models.project_communication import (
    ProjectCommunication,
    COMMUNICATION_CATEGORIES,
)
from app.models.project import Project
from app.services.communication_ingest import extract_trades, ingest_communication


router = APIRouter(prefix="/projects/{project_id}/communications", tags=["communications"])


class CommCreate(BaseModel):
    body: str
    subject: str | None = None
    from_name: str | None = None
    from_email: str | None = None
    to_emails: str | None = None
    channel: str | None = "email"          # email | sms | call | meeting | note
    direction: str | None = None           # inbound | outbound | internal
    occurred_at: str | None = None         # ISO datetime/date
    classify: bool = True


class CommUpdate(BaseModel):
    category: str | None = None
    summary: str | None = None
    urgency: str | None = None
    direction: str | None = None
    has_quote: bool | None = None


def comm_read(c: ProjectCommunication) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "source": c.source,
        "direction": c.direction,
        "channel": c.channel,
        "from_name": c.from_name,
        "from_email": c.from_email,
        "to_emails": c.to_emails,
        "subject": c.subject,
        "body": c.body,
        "occurred_at": c.occurred_at.isoformat() if c.occurred_at else None,
        "category": c.category,
        "summary": c.summary,
        "action_items": c.action_items or [],
        "urgency": c.urgency,
        "has_quote": c.has_quote,
        "processed": c.processed_at is not None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _uid(user: CurrentUser) -> UUID | None:
    return real_user_id(user)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            from datetime import date as _d
            return datetime.combine(_d.fromisoformat(s[:10]), datetime.min.time(), tzinfo=timezone.utc)
        except ValueError:
            return None


def _get(db: Session, project_id: UUID, cid: UUID) -> ProjectCommunication:
    c = db.get(ProjectCommunication, cid)
    if c is None or c.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="communication not found")
    return c


@router.get("")
def list_communications(
    project_id: UUID,
    category: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> list[dict]:
    assert_project_access(db, user, project_id)
    q = select(ProjectCommunication).where(ProjectCommunication.project_id == project_id)
    if category and category in COMMUNICATION_CATEGORIES:
        q = q.where(ProjectCommunication.category == category)
    q = q.order_by(
        ProjectCommunication.occurred_at.desc().nullslast(),
        ProjectCommunication.created_at.desc(),
    )
    return [comm_read(c) for c in db.execute(q).scalars().all()]


@router.get("/highlights")
def highlights(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """The 'what's latest' block: most-recent items, open action items, and
    anything flagged high-urgency — so a manager sees the current state fast."""
    assert_project_access(db, user, project_id)
    rows = db.execute(
        select(ProjectCommunication).where(ProjectCommunication.project_id == project_id)
        .order_by(ProjectCommunication.occurred_at.desc().nullslast(),
                  ProjectCommunication.created_at.desc())
        .limit(40)
    ).scalars().all()
    recent = [comm_read(c) for c in rows[:5]]
    open_actions: list[dict] = []
    for c in rows:
        for it in (c.action_items or []):
            open_actions.append({"item": it, "communication_id": str(c.id),
                                 "category": c.category, "occurred_at":
                                 c.occurred_at.isoformat() if c.occurred_at else None})
    urgent = [comm_read(c) for c in rows if c.urgency == "high"][:5]
    # category counts
    counts: dict[str, int] = {}
    for c in db.execute(
        text("SELECT category, COUNT(*) FROM project_communications "
             "WHERE project_id = :p GROUP BY category"), {"p": project_id},
    ).all():
        counts[c[0] or "other"] = int(c[1])
    return {"recent": recent, "open_action_items": open_actions[:15],
            "urgent": urgent, "category_counts": counts}


@router.get("/inbound-address")
def inbound_address(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """The per-project BCC/forward address. Lazily mints the token on first ask."""
    assert_project_access(db, user, project_id)
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="project not found")
    if not p.inbound_token:
        p.inbound_token = secrets.token_hex(6)  # 12 hex chars, address-safe
        db.commit()
        db.refresh(p)
    return {
        "address": f"p-{p.inbound_token}@{settings.inbound_email_domain}",
        "token": p.inbound_token,
        "domain": settings.inbound_email_domain,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_communication(
    project_id: UUID,
    body: CommCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    assert_project_access(db, user, project_id)
    if not (body.body or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="body is required")

    c = ingest_communication(
        db,
        project_id=project_id,
        body=body.body,
        subject=body.subject,
        from_name=body.from_name,
        from_email=body.from_email,
        to_emails=body.to_emails,
        channel=body.channel,
        direction=body.direction,
        source="manual",
        occurred_at=_parse_dt(body.occurred_at),
        classify=body.classify,
        created_by_id=_uid(user),
    )
    return comm_read(c)


@router.post("/{cid}/reclassify")
def reclassify(
    project_id: UUID,
    cid: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    assert_project_access(db, user, project_id)
    c = _get(db, project_id, cid)
    from app.services.communication_classify import classify_communication
    ai = classify_communication(c.subject, c.body, c.from_email)
    c.category, c.summary, c.urgency = ai["category"], ai["summary"], ai["urgency"]
    c.action_items, c.has_quote = ai["action_items"], ai["has_quote"]
    if ai.get("direction"):
        c.direction = ai["direction"]
    c.processed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(c)
    extract_trades(db, ai.get("trades"), c)
    return comm_read(c)


@router.patch("/{cid}")
def update_communication(
    project_id: UUID,
    cid: UUID,
    body: CommUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    assert_project_access(db, user, project_id)
    c = _get(db, project_id, cid)
    if body.category is not None and body.category in COMMUNICATION_CATEGORIES:
        c.category = body.category
    if body.summary is not None:
        c.summary = body.summary
    if body.urgency is not None and body.urgency in ("low", "normal", "high"):
        c.urgency = body.urgency
    if body.direction is not None:
        c.direction = body.direction
    if body.has_quote is not None:
        c.has_quote = body.has_quote
    db.commit()
    db.refresh(c)
    return comm_read(c)


@router.delete("/{cid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_communication(
    project_id: UUID,
    cid: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
):
    assert_project_access(db, user, project_id)
    db.delete(_get(db, project_id, cid))
    db.commit()
