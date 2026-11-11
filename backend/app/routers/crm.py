"""CRM HTTP routes — the queue, contacts, opportunities and interactions.

The queue endpoint is the product. Everything else exists to feed it or to
record what came out of it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import CurrentUser, get_current_user, require_superadmin
from app.db import get_db
from app.models.client import Client
from app.models.crm import (
    CRM_CHANNELS,
    CRM_DIRECTIONS,
    CRM_OPEN_STATUSES,
    CRM_STATUSES,
    CRM_TRACKS,
    CrmInteraction,
    CrmOpportunity,
)
from app.models.user import User
from app.schemas.crm import (
    ContactWithOpportunities,
    CrmContactBase,
    CrmInteractionRead,
    CrmOpportunityRead,
    LogInteraction,
    OpportunityCreate,
    OpportunityUpdate,
    QueueItem,
    QueueResponse,
)

# Superadmin only (Amir, 2026-10-15). Until then every route here checked only
# that someone was signed in, so a manager, a crew member or a client (owner)
# account could read it. Router-level, so a route added later is covered.
router = APIRouter(prefix="/crm", tags=["crm"], dependencies=[Depends(require_superadmin)])


def _channel(c: Client) -> tuple[str, str | None, str | None]:
    """Which channel to use for this firm, and what to do with it.

    Derived from the contact record every time it is read. Storing a channel on
    the opportunity at import time is what produced "Call — they file 20+ permits
    a year" against firms holding no phone number: the instruction came from the
    tier and never looked at whether it was possible.
    """
    if c.phone:
        return ("call", c.phone,
                "Best channel for a firm this active — a two-minute call beats "
                "an email they will not open.")
    if c.email:
        return ("email", c.email,
                "No phone on file. Email, and ask for a call in the reply rather "
                "than pitching in the first message.")
    if c.website:
        return ("website", c.website,
                "No direct address. Their site usually has a contact form or a "
                "named principal — that name is worth more than the form.")
    return ("find_contact", None,
            "No contact details found. Design firms are visual businesses, so "
            "Instagram is often the fastest route — search the firm name, then "
            "LinkedIn, then the firm name plus 'Vancouver'. Ten minutes maximum; "
            "if nothing surfaces, log it and move on.")


def _validate(value: str | None, allowed: tuple[str, ...], field: str) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{field} must be one of {', '.join(allowed)}",
        )


# ── The week ──────────────────────────────────────────────────────────────

@router.get("/queue", response_model=QueueResponse)
def queue(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    track: Optional[str] = Query(None),
    owner: Optional[str] = Query(None, description="'me', a user id, or omit for all"),
    horizon_days: int = Query(0, ge=0, le=90,
                              description="also include actions due this many days ahead"),
    include_unactioned: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
):
    """Everything needing attention: overdue, due today, and missing a next action.

    Sorted so the most overdue is first — an item that has been waiting longest
    is the one most likely to go cold.
    """
    today = date.today()
    cutoff = today + timedelta(days=horizon_days)

    q = (
        select(CrmOpportunity, Client, User.name)
        .join(Client, Client.id == CrmOpportunity.client_id)
        .outerjoin(User, User.id == CrmOpportunity.owner_id)
        .where(CrmOpportunity.status.in_(CRM_OPEN_STATUSES))
        .where(Client.excluded_reason.is_(None))
    )
    if track:
        _validate(track, CRM_TRACKS, "track")
        q = q.where(CrmOpportunity.track == track)
    if owner == "me":
        q = q.where(CrmOpportunity.owner_id == user.id)
    elif owner:
        q = q.where(CrmOpportunity.owner_id == UUID(owner))

    due = CrmOpportunity.next_action_at <= cutoff
    if include_unactioned:
        q = q.where(or_(due, CrmOpportunity.next_action_at.is_(None)))
    else:
        q = q.where(due)

    # nulls first — an opportunity with no next action is the most urgent thing
    # in the list, because it is silently going nowhere
    q = q.order_by(CrmOpportunity.next_action_at.asc().nullsfirst()).limit(limit)

    rows = db.execute(q).all()
    opp_ids = [r[0].id for r in rows]

    counts_by_opp: dict[UUID, int] = {}
    last_by_opp: dict[UUID, CrmInteraction] = {}
    if opp_ids:
        for oid, n in db.execute(
            select(CrmInteraction.opportunity_id, func.count())
            .where(CrmInteraction.opportunity_id.in_(opp_ids))
            .group_by(CrmInteraction.opportunity_id)
        ).all():
            counts_by_opp[oid] = n
        for it in db.execute(
            select(CrmInteraction)
            .where(CrmInteraction.opportunity_id.in_(opp_ids))
            .order_by(CrmInteraction.occurred_at.desc())
        ).scalars():
            last_by_opp.setdefault(it.opportunity_id, it)

    items: list[QueueItem] = []
    counts = {"overdue": 0, "due_today": 0, "no_action": 0, "upcoming": 0,
              "call": 0, "email": 0, "website": 0, "find_contact": 0}
    for opp, client, owner_name in rows:
        if opp.next_action_at is None:
            reason, overdue = "no_action", 0
        elif opp.next_action_at < today:
            reason, overdue = "overdue", (today - opp.next_action_at).days
        elif opp.next_action_at == today:
            reason, overdue = "due_today", 0
        else:
            reason, overdue = "upcoming", 0
        counts[reason] += 1
        counts[_channel(client)[0]] += 1
        last = last_by_opp.get(opp.id)
        ch, target, hint = _channel(client)
        items.append(QueueItem(
            opportunity=CrmOpportunityRead.model_validate(opp),
            contact=CrmContactBase.model_validate(client),
            owner_name=owner_name,
            last_interaction=CrmInteractionRead.model_validate(last) if last else None,
            interaction_count=counts_by_opp.get(opp.id, 0),
            reason=reason,
            days_overdue=overdue,
            channel=ch,
            channel_target=target,
            channel_hint=hint,
        ))
    return QueueResponse(items=items, counts=counts)


# ── Contacts ──────────────────────────────────────────────────────────────

@router.get("/contacts", response_model=list[CrmContactBase])
def list_contacts(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    tier: Optional[str] = Query(None, description="one or more, e.g. 'AB'"),
    segment: Optional[str] = Query(None),
    confidence: Optional[str] = Query(None),
    has_contact: Optional[bool] = Query(None),
    city: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="name search"),
    include_excluded: bool = Query(False),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    sel = select(Client).where(Client.kind == "prospect")
    if tier:
        sel = sel.where(Client.tier.in_(list(tier.upper())))
    if segment:
        sel = sel.where(Client.segment == segment)
    if confidence:
        sel = sel.where(Client.contact_confidence == confidence)
    if has_contact is True:
        sel = sel.where(or_(Client.email.isnot(None), Client.phone.isnot(None)))
    elif has_contact is False:
        sel = sel.where(Client.email.is_(None), Client.phone.is_(None))
    if city:
        sel = sel.where(Client.cities.ilike(f"%{city}%"))
    if q:
        sel = sel.where(Client.normalized_name.ilike(f"%{q.lower()}%"))
    if not include_excluded:
        sel = sel.where(Client.excluded_reason.is_(None))

    sel = sel.order_by(
        Client.tier.asc().nullslast(),
        Client.permits_total.desc().nullslast(),
    ).limit(limit).offset(offset)
    return list(db.execute(sel).scalars())


@router.get("/contacts/{client_id}", response_model=ContactWithOpportunities)
def get_contact(
    client_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    c = db.get(Client, client_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
    opps = list(db.execute(
        select(CrmOpportunity).where(CrmOpportunity.client_id == client_id)
    ).scalars())
    ints = list(db.execute(
        select(CrmInteraction)
        .where(CrmInteraction.client_id == client_id)
        .order_by(CrmInteraction.occurred_at.desc())
        .limit(50)
    ).scalars())
    out = ContactWithOpportunities.model_validate(c)
    out.opportunities = [CrmOpportunityRead.model_validate(o) for o in opps]
    out.interactions = [CrmInteractionRead.model_validate(i) for i in ints]
    return out


@router.post("/contacts/{client_id}/opportunities",
             response_model=CrmOpportunityRead,
             status_code=status.HTTP_201_CREATED)
def promote(
    client_id: UUID,
    payload: OpportunityCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Promote a contact into the queue — how Tier C/D firms get worked."""
    c = db.get(Client, client_id)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
    if c.excluded_reason:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"contact is excluded: {c.excluded_reason}")
    _validate(payload.track, CRM_TRACKS, "track")
    existing = db.execute(
        select(CrmOpportunity).where(CrmOpportunity.client_id == client_id,
                                     CrmOpportunity.track == payload.track)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "an opportunity already exists on that track")
    opp = CrmOpportunity(
        client_id=client_id, track=payload.track, status="new",
        next_action=payload.next_action or "Make contact",
        next_action_at=payload.next_action_at or date.today(),
        owner_id=payload.owner_id or user.id,
    )
    db.add(opp)
    db.commit()
    db.refresh(opp)
    return opp


# ── Opportunities ─────────────────────────────────────────────────────────

@router.patch("/opportunities/{opp_id}", response_model=CrmOpportunityRead)
def update_opportunity(
    opp_id: UUID,
    payload: OpportunityUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    opp = db.get(CrmOpportunity, opp_id)
    if not opp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found")
    _validate(payload.status, CRM_STATUSES, "status")
    for f in ("status", "next_action", "next_action_at", "owner_id", "notes"):
        v = getattr(payload, f)
        if v is not None:
            setattr(opp, f, v)
    db.commit()
    db.refresh(opp)
    return opp


@router.post("/opportunities/{opp_id}/log", response_model=CrmOpportunityRead)
def log_interaction(
    opp_id: UUID,
    payload: LogInteraction,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Record what happened and set what happens next, in one call.

    The next action is part of the same payload deliberately. A logged call with
    no next step is how an opportunity goes quiet without anyone noticing.
    """
    opp = db.get(CrmOpportunity, opp_id)
    if not opp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found")
    _validate(payload.channel, CRM_CHANNELS, "channel")
    _validate(payload.direction, CRM_DIRECTIONS, "direction")
    _validate(payload.status, CRM_STATUSES, "status")

    now = datetime.now(timezone.utc)
    db.add(CrmInteraction(
        client_id=opp.client_id, opportunity_id=opp.id,
        channel=payload.channel, direction=payload.direction,
        occurred_at=payload.occurred_at or now,
        summary=payload.summary, outcome=payload.outcome,
        created_by_id=user.id,
    ))
    opp.last_contacted_at = payload.occurred_at or now
    if payload.status:
        opp.status = payload.status
    elif opp.status == "new":
        opp.status = "working"          # first contact moves it out of 'new'
    # Clearing the next action is allowed only by closing the opportunity;
    # otherwise the item would vanish from the queue while still open.
    if payload.next_action is not None:
        opp.next_action = payload.next_action
    if payload.next_action_at is not None:
        opp.next_action_at = payload.next_action_at
    elif opp.status in CRM_OPEN_STATUSES and payload.next_action_at is None:
        opp.next_action_at = None       # surfaces as 'no_action' — deliberately loud

    db.commit()
    db.refresh(opp)
    return opp
