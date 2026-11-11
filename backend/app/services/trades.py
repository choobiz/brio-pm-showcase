"""Trade Library service — normalization, dedup, and find-or-create.

The library is cross-project. Dedup priority mirrors the Client CRM:
email > phone > normalized company/contact name. ``find_or_create_trade`` is
used both by manual creates and by the Conversations extraction hook; the
latter passes ``source='communication'`` so new rows land as ``unconfirmed``.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.trade import Trade, TRADE_CATEGORIES


def normalize_name(s: str | None) -> str:
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"\b(inc|ltd|llc|corp|co|company|construction|contracting|services|group|and|&)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_email(s: str | None) -> str:
    return (s or "").strip().lower()


def normalize_phone(s: str | None) -> str:
    return re.sub(r"\D+", "", s or "")


def canonical_category(c: str | None) -> str:
    c = (c or "").strip().lower().replace("-", "_").replace(" ", "_")
    return c if c in TRADE_CATEGORIES else "other"


def _display_name(company: str | None, contact: str | None) -> str:
    return (company or contact or "").strip()


def trade_read(t: Trade) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "company_name": t.company_name,
        "contact_name": t.contact_name,
        "trade_category": t.trade_category,
        "email": t.email,
        "phone": t.phone,
        "website": t.website,
        "tags": t.tags or [],
        "notes": t.notes,
        "rating": t.rating,
        "status": t.status,
        "source": t.source,
        "source_communication_id": str(t.source_communication_id) if t.source_communication_id else None,
        "first_project_id": str(t.first_project_id) if t.first_project_id else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _match_existing(db: Session, ne: str, np: str, nn: str) -> Trade | None:
    """Find an existing trade by email, then phone, then normalized name."""
    if ne:
        hit = db.execute(
            select(Trade).where(Trade.normalized_emails.any(ne))
        ).scalars().first()
        if hit:
            return hit
    if np:
        hit = db.execute(
            select(Trade).where(Trade.normalized_phones.any(np))
        ).scalars().first()
        if hit:
            return hit
    if nn:
        hit = db.execute(
            select(Trade).where(Trade.normalized_name == nn)
        ).scalars().first()
        if hit:
            return hit
    return None


def find_or_create_trade(
    db: Session,
    *,
    company_name: str | None = None,
    contact_name: str | None = None,
    trade_category: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    website: str | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
    rating: int | None = None,
    source: str = "manual",
    source_communication_id: UUID | None = None,
    first_project_id: UUID | None = None,
    created_by_id: UUID | None = None,
    commit: bool = True,
) -> tuple[Trade, bool]:
    """Return (trade, created). Dedups by email > phone > normalized name.

    On a match, missing contact fields are enriched but an existing status is
    NOT downgraded (a confirmed trade stays confirmed even if a later
    extraction re-surfaces it).
    """
    email = (email or "").strip() or None
    phone = (phone or "").strip() or None
    ne, np = normalize_email(email), normalize_phone(phone)
    nn = normalize_name(_display_name(company_name, contact_name))
    cat = canonical_category(trade_category)

    existing = _match_existing(db, ne, np, nn)
    if existing is not None:
        # Enrich blanks only.
        if not existing.company_name and company_name:
            existing.company_name = company_name.strip()
        if not existing.contact_name and contact_name:
            existing.contact_name = contact_name.strip()
        if existing.trade_category == "other" and cat != "other":
            existing.trade_category = cat
        if not existing.email and email:
            existing.email = email
            if ne and ne not in existing.normalized_emails:
                existing.emails = [*(existing.emails or []), email]
                existing.normalized_emails = [*(existing.normalized_emails or []), ne]
        if not existing.phone and phone:
            existing.phone = phone
            if np and np not in existing.normalized_phones:
                existing.phones = [*(existing.phones or []), phone]
                existing.normalized_phones = [*(existing.normalized_phones or []), np]
        if not existing.website and website:
            existing.website = website.strip()
        if not existing.normalized_name and nn:
            existing.normalized_name = nn
        if commit:
            db.commit()
            db.refresh(existing)
        return existing, False

    status = "unconfirmed" if source == "communication" else "active"
    t = Trade(
        company_name=(company_name or "").strip() or None,
        contact_name=(contact_name or "").strip() or None,
        normalized_name=nn,
        trade_category=cat,
        email=email,
        phone=phone,
        website=(website or "").strip() or None,
        emails=[email] if email else [],
        normalized_emails=[ne] if ne else [],
        phones=[phone] if phone else [],
        normalized_phones=[np] if np else [],
        tags=tags or [],
        notes=(notes or "").strip() or None,
        rating=rating,
        status=status,
        source=source,
        source_communication_id=source_communication_id,
        first_project_id=first_project_id,
        created_by_id=created_by_id,
    )
    db.add(t)
    if commit:
        db.commit()
        db.refresh(t)
    return t, True
