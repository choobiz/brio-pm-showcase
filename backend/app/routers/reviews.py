"""Review solicitation API — draft + log Google-review asks, and read live stats.

  POST /reviews/generate   draft a message (no store) — used by "Generate" / "Recreate"
  POST /reviews            log a request (when the owner sends it)
  GET  /reviews            recent requests (log)
  GET  /reviews/stats      live rating + review count + write-review link (cached)
  PATCH /reviews/{id}/sent mark a logged request as sent on a channel
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_superadmin
from app.db import get_db
from app.models.review_request import ReviewRequest, PERSON_TYPES, REVIEW_CHANNELS
from app.services import reviews as svc


# Superadmin only (Amir, 2026-10-15). Until then every route here checked only
# that someone was signed in, so a manager, a crew member or a client (owner)
# account could read it. Router-level, so a route added later is covered.
router = APIRouter(prefix="/reviews", tags=["reviews"], dependencies=[Depends(require_superadmin)])


class GenerateIn(BaseModel):
    recipient_name: str
    person_type: str = "customer"
    note: str | None = None
    variation: int = 0


class CreateIn(BaseModel):
    recipient_name: str
    person_type: str = "customer"
    email: str | None = None
    phone: str | None = None
    note: str | None = None
    message: str
    channel: str | None = None


def _out(r: ReviewRequest) -> dict:
    return {
        "id": str(r.id),
        "recipient_name": r.recipient_name,
        "person_type": r.person_type,
        "email": r.email,
        "phone": r.phone,
        "note": r.note,
        "message": r.message,
        "channel": r.channel,
        "status": r.status,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
        "requester_name": r.requester_name,
        "created_at": r.created_at.isoformat(),
    }


@router.get("/stats")
def stats(_: CurrentUser = Depends(get_current_user)) -> dict:
    return svc.review_stats()


@router.post("/generate")
def generate(body: GenerateIn, _: CurrentUser = Depends(get_current_user)) -> dict:
    if not body.recipient_name.strip():
        raise HTTPException(status_code=400, detail="Recipient name is required.")
    pt = body.person_type if body.person_type in PERSON_TYPES else "customer"
    message = svc.draft_message(body.recipient_name, pt, body.note, max(0, body.variation))
    return {"message": message}


@router.post("")
def create(body: CreateIn, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if not body.recipient_name.strip() or not body.message.strip():
        raise HTTPException(status_code=400, detail="Recipient and message are required.")
    channel = body.channel if body.channel in REVIEW_CHANNELS else None
    r = ReviewRequest(
        requested_by=UUID(user.id) if user.id else None,
        requester_name=user.email,
        recipient_name=body.recipient_name.strip(),
        person_type=body.person_type if body.person_type in PERSON_TYPES else "customer",
        email=(body.email or None),
        phone=(body.phone or None),
        note=(body.note or None),
        message=body.message.strip(),
        channel=channel,
        status="sent" if channel else "draft",
        sent_at=datetime.now(timezone.utc) if channel else None,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _out(r)


@router.get("")
def recent(_: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(ReviewRequest).order_by(ReviewRequest.created_at.desc()).limit(50)).scalars().all()
    return [_out(r) for r in rows]


@router.patch("/{rid}/sent")
def mark_sent(rid: UUID, channel: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    r = db.get(ReviewRequest, rid)
    if not r:
        raise HTTPException(status_code=404, detail="Not found.")
    r.channel = channel if channel in REVIEW_CHANNELS else r.channel
    r.status = "sent"
    r.sent_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(r)
    return _out(r)
