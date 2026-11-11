"""Trade Library — cross-project directory of trades / subcontractors / suppliers.

Office-only (require_office). Not project-scoped. AI-extracted rows arrive via
the Conversations hook (see routers/communications.py) as ``unconfirmed`` and
are confirmed here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_office, real_user_id
from app.db import get_db
from app.models.trade import Trade, TRADE_CATEGORIES, TRADE_STATUSES
from app.services.trades import (
    canonical_category,
    find_or_create_trade,
    normalize_email,
    normalize_name,
    normalize_phone,
    trade_read,
)


router = APIRouter(prefix="/trades", tags=["trades"])


class TradeCreate(BaseModel):
    company_name: str | None = None
    contact_name: str | None = None
    trade_category: str | None = "other"
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    rating: int | None = None


class TradeUpdate(BaseModel):
    company_name: str | None = None
    contact_name: str | None = None
    trade_category: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    rating: int | None = None
    status: str | None = None


def _uid(user: CurrentUser) -> UUID | None:
    return real_user_id(user)


def _get(db: Session, tid: UUID) -> Trade:
    t = db.get(Trade, tid)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="trade not found")
    return t


@router.get("")
def list_trades(
    q: str | None = None,
    category: str | None = None,
    status_filter: str | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> list[dict]:
    stmt = select(Trade)
    if category and category in TRADE_CATEGORIES:
        stmt = stmt.where(Trade.trade_category == category)
    if status_filter and status_filter in TRADE_STATUSES:
        stmt = stmt.where(Trade.status == status_filter)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(func.coalesce(Trade.company_name, ""))
            .op("||")(func.lower(func.coalesce(Trade.contact_name, "")))
            .op("||")(func.coalesce(Trade.email, ""))
            .op("||")(func.coalesce(Trade.phone, ""))
            .op("||")(func.coalesce(Trade.notes, ""))
            .ilike(like)
        )
    # Unconfirmed first (they need review), then by name.
    stmt = stmt.order_by(
        (Trade.status == "unconfirmed").desc(),
        func.lower(func.coalesce(Trade.company_name, Trade.contact_name, "")).asc(),
    )
    return [trade_read(t) for t in db.execute(stmt).scalars().all()]


@router.get("/summary")
def summary(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Counts for the directory chips (status + per-category)."""
    status_counts: dict[str, int] = {}
    for s, n in db.execute(
        select(Trade.status, func.count()).group_by(Trade.status)
    ).all():
        status_counts[s or "active"] = int(n)
    category_counts: dict[str, int] = {}
    for c, n in db.execute(
        select(Trade.trade_category, func.count()).group_by(Trade.trade_category)
    ).all():
        category_counts[c or "other"] = int(n)
    return {"status_counts": status_counts, "category_counts": category_counts}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_trade(
    body: TradeCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    if not (body.company_name or body.contact_name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="company_name or contact_name is required")
    trade, _created = find_or_create_trade(
        db,
        company_name=body.company_name,
        contact_name=body.contact_name,
        trade_category=body.trade_category,
        email=body.email,
        phone=body.phone,
        website=body.website,
        notes=body.notes,
        tags=body.tags,
        rating=body.rating,
        source="manual",
        created_by_id=_uid(user),
    )
    return trade_read(trade)


@router.get("/{tid}")
def get_trade(
    tid: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    return trade_read(_get(db, tid))


@router.patch("/{tid}")
def update_trade(
    tid: UUID,
    body: TradeUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    t = _get(db, tid)
    if body.company_name is not None:
        t.company_name = body.company_name.strip() or None
    if body.contact_name is not None:
        t.contact_name = body.contact_name.strip() or None
    if body.company_name is not None or body.contact_name is not None:
        t.normalized_name = normalize_name(t.company_name or t.contact_name)
    if body.trade_category is not None:
        t.trade_category = canonical_category(body.trade_category)
    if body.email is not None:
        t.email = body.email.strip() or None
        ne = normalize_email(t.email)
        t.emails = [t.email] if t.email else []
        t.normalized_emails = [ne] if ne else []
    if body.phone is not None:
        t.phone = body.phone.strip() or None
        np = normalize_phone(t.phone)
        t.phones = [t.phone] if t.phone else []
        t.normalized_phones = [np] if np else []
    if body.website is not None:
        t.website = body.website.strip() or None
    if body.notes is not None:
        t.notes = body.notes.strip() or None
    if body.tags is not None:
        t.tags = body.tags
    if body.rating is not None:
        t.rating = body.rating
    if body.status is not None and body.status in TRADE_STATUSES:
        t.status = body.status
    db.commit()
    db.refresh(t)
    return trade_read(t)


@router.post("/{tid}/confirm")
def confirm_trade(
    tid: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Promote an AI-extracted (unconfirmed) trade into the active directory."""
    t = _get(db, tid)
    t.status = "active"
    db.commit()
    db.refresh(t)
    return trade_read(t)


@router.delete("/{tid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_trade(
    tid: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
):
    db.delete(_get(db, tid))
    db.commit()
