"""API spend for Ask and prepared documents, and the monthly cap.

The cap ($8/month, Amir 2026-10-13) covers API spend on PREPARING documents,
which only happens when the Mac worker is unavailable. Ask questions are
recorded too, so the month's total is visible, but they are not stopped by it:
a few cents each, and a question that refuses to answer is worse than useless.

Only the accounts in `PREP_CAP_OWNER_EMAILS` may change the cap.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.config import settings
from app.models.prep import AISpend, AppSetting

CAP_KEY = "prep_api_monthly_cap_usd"
DEFAULT_CAP = Decimal("8")

#: USD per million tokens (docs.claude.com pricing, fetched 2026-10-13).
#: Cache reads on the 5.5 models are 5% of input; 5-minute cache writes 1.25x.
PRICES: dict[str, dict[str, Decimal]] = {
    "claude-opus-5-5": {"in": Decimal("4"), "out": Decimal("20"), "read": Decimal("0.20"), "write": Decimal("5")},
    "claude-sonnet-5-5": {"in": Decimal("2"), "out": Decimal("10"), "read": Decimal("0.10"), "write": Decimal("2.50")},
    "claude-haiku-5-5": {"in": Decimal("0.10"), "out": Decimal("0.50"), "read": Decimal("0.01"), "write": Decimal("0.125")},
}
_M = Decimal(1_000_000)


def cost(model: str, usage: Any) -> Decimal:
    p = PRICES.get(model) or PRICES["claude-opus-5-5"]  # unknown model: price it high, never free
    g = (lambda k: int(getattr(usage, k, None) or (usage.get(k) if isinstance(usage, dict) else 0) or 0))
    return ((g("input_tokens") * p["in"] + g("output_tokens") * p["out"]
             + g("cache_read_input_tokens") * p["read"] + g("cache_creation_input_tokens") * p["write"]) / _M
            ).quantize(Decimal("0.0001"))


def record(db: Session, purpose: str, model: str, usage: Any, *, prep_request_id: UUID | None = None,
           stage: str | None = None) -> Decimal:
    g = (lambda k: int(getattr(usage, k, None) or (usage.get(k) if isinstance(usage, dict) else 0) or 0))
    c = cost(model, usage)
    db.add(AISpend(purpose=purpose, model=model, prep_request_id=prep_request_id, stage=stage,
                   input_tokens=g("input_tokens"), output_tokens=g("output_tokens"),
                   cache_read_tokens=g("cache_read_input_tokens"),
                   cache_write_tokens=g("cache_creation_input_tokens"), cost_usd=c))
    db.flush()
    return c


def _month_start(now: datetime | None = None) -> datetime:
    from zoneinfo import ZoneInfo

    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/Vancouver"))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_spend(db: Session, purpose: str | None = None, *, now: datetime | None = None) -> Decimal:
    q = "SELECT coalesce(sum(cost_usd), 0) FROM ai_spend WHERE created_at >= :m"
    params: dict[str, Any] = {"m": _month_start(now)}
    if purpose:
        q += " AND purpose = :p"
        params["p"] = purpose
    return Decimal(db.execute(_T(q), params).scalar() or 0)


def cap(db: Session) -> Decimal:
    row = db.get(AppSetting, CAP_KEY)
    try:
        return Decimal(str(row.value["usd"])) if row else DEFAULT_CAP
    except (KeyError, TypeError, ValueError):
        return DEFAULT_CAP


def remaining(db: Session) -> Decimal:
    return cap(db) - month_spend(db, "prep")


def can_change_cap(user: CurrentUser) -> bool:
    return (user.email or "").lower() in settings.prep_cap_owner_emails


def set_cap(db: Session, user: CurrentUser, usd: Decimal) -> Decimal:
    if not can_change_cap(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only Amir can change the monthly AI budget.")
    if usd < 0 or usd > 500:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="The budget must be between $0 and $500.")
    row = db.get(AppSetting, CAP_KEY)
    if row is None:
        row = AppSetting(key=CAP_KEY, value={})
        db.add(row)
    row.value = {"usd": float(usd)}
    row.updated_at = datetime.now(timezone.utc)
    try:
        row.updated_by_id = UUID(str(user.id))
    except (TypeError, ValueError):
        row.updated_by_id = None
    db.flush()
    return usd


def summary(db: Session, user: CurrentUser) -> dict[str, Any]:
    c, prep, ask = cap(db), month_spend(db, "prep"), month_spend(db, "ask")
    return {"month_start": _month_start().date().isoformat(), "cap_usd": float(c),
            "prep_usd": float(prep), "ask_usd": float(ask), "remaining_usd": float(max(c - prep, 0)),
            "can_change_cap": can_change_cap(user)}
