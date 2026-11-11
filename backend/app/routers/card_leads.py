"""Business-card leads — "send me your details" from builtbybrio.com/card/<slug>.

Two routers:

* ``public`` — ``POST /public/card-leads``. No user auth; the website's server
  route authenticates with ``CARD_INTAKE_KEY`` carried in the JSON body (the
  reverse proxy drops unknown headers, so a header would never arrive).
* ``router`` — ``/crm/card-leads`` list + status change, superadmin only like
  the rest of the CRM.
"""

from __future__ import annotations

import hmac
import re
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_superadmin
from app.config import settings
from app.db import get_db
from app.models.crm import CARD_LEAD_ROLES, CARD_LEAD_STATUSES, CrmCardLead

public = APIRouter(prefix="/public", tags=["public"])
router = APIRouter(prefix="/crm", tags=["crm"], dependencies=[Depends(require_superadmin)])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# field -> max length after trimming
_LIMITS = {
    "name": 120, "company": 160, "phone": 40, "email": 200, "note": 1000,
    "card_slug": 60, "source": 40,
}
_USER_AGENT_MAX = 500


class CardLeadIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intake_key: Optional[str] = None
    name: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    note: Optional[str] = None
    card_slug: Optional[str] = None
    source: Optional[str] = None
    user_agent: Optional[str] = None
    website: Optional[str] = None  # honeypot — humans never see this field


class CardLeadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    name: str
    company: Optional[str] = None
    role: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    note: Optional[str] = None
    card_slug: str
    source: Optional[str] = None
    status: str
    contacted_at: Optional[datetime] = None


class CardLeadUpdate(BaseModel):
    status: str


def _clean(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    return v or None


def _bad(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, msg)


@public.post("/card-leads")
def create_card_lead(body: CardLeadIn, db: Session = Depends(get_db)) -> dict:
    expected = settings.card_intake_key
    given = body.intake_key or ""
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")

    # Honeypot: a bot filled the hidden field. Look successful, store nothing.
    if (body.website or "").strip():
        return {"ok": True}

    vals = {f: _clean(getattr(body, f)) for f in _LIMITS}
    for f, limit in _LIMITS.items():
        if vals[f] is not None and len(vals[f]) > limit:
            raise _bad(f"{f} must be at most {limit} characters")
    if not vals["name"]:
        raise _bad("name is required")
    if not vals["card_slug"]:
        raise _bad("card_slug is required")
    if not vals["phone"] and not vals["email"]:
        raise _bad("a phone number or an email is required")
    if vals["email"] and not _EMAIL_RE.match(vals["email"]):
        raise _bad("email does not look like an email address")

    role = _clean(body.role)
    role = role.lower() if role else None
    if role is not None and role not in CARD_LEAD_ROLES:
        raise _bad(f"role must be one of {', '.join(CARD_LEAD_ROLES)}")

    ua = _clean(body.user_agent)
    db.add(CrmCardLead(
        name=vals["name"], company=vals["company"], role=role,
        phone=vals["phone"], email=vals["email"], note=vals["note"],
        card_slug=vals["card_slug"].lower(), source=vals["source"],
        user_agent=ua[:_USER_AGENT_MAX] if ua else None, status="new",
    ))
    db.commit()
    return {"ok": True}


@router.get("/card-leads", response_model=list[CardLeadRead])
def list_card_leads(
    status_: Optional[str] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
) -> list[CrmCardLead]:
    q = select(CrmCardLead).order_by(CrmCardLead.created_at.desc())
    if status_:
        if status_ not in CARD_LEAD_STATUSES:
            raise _bad(f"status must be one of {', '.join(CARD_LEAD_STATUSES)}")
        q = q.where(CrmCardLead.status == status_)
    return list(db.execute(q).scalars().all())


@router.patch("/card-leads/{lead_id}", response_model=CardLeadRead)
def update_card_lead(lead_id: UUID, body: CardLeadUpdate, db: Session = Depends(get_db)) -> CrmCardLead:
    if body.status not in CARD_LEAD_STATUSES:
        raise _bad(f"status must be one of {', '.join(CARD_LEAD_STATUSES)}")
    lead = db.get(CrmCardLead, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "card lead not found")
    lead.status = body.status
    if body.status == "contacted":
        lead.contacted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(lead)
    return lead
