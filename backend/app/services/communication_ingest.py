"""Shared communication ingestion — used by both the manual office create and
the inbound-email webhook (Conversations P2).

Persists a ProjectCommunication, runs best-effort AI classification, and feeds
any extracted trade contacts into the Trade Library as ``unconfirmed`` rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.project_communication import ProjectCommunication
from app.services.trades import find_or_create_trade


def extract_trades(db: Session, ai_trades: Any, c: ProjectCommunication) -> None:
    """Best-effort: feed AI-extracted trade contacts into the Trade Library.

    New rows land as ``unconfirmed`` (source='communication'); existing rows are
    deduped/enriched. Never raises — extraction must not break the log.
    """
    if not isinstance(ai_trades, list) or not ai_trades:
        return
    for t in ai_trades:
        if not isinstance(t, dict):
            continue
        try:
            find_or_create_trade(
                db,
                company_name=t.get("company_name"),
                contact_name=t.get("contact_name"),
                trade_category=t.get("trade_category"),
                email=t.get("email"),
                phone=t.get("phone"),
                source="communication",
                source_communication_id=c.id,
                first_project_id=c.project_id,
                created_by_id=c.created_by_id,
            )
        except Exception:  # noqa: BLE001 — extraction is best-effort
            db.rollback()


def ingest_communication(
    db: Session,
    *,
    project_id: UUID,
    body: str,
    subject: str | None = None,
    from_name: str | None = None,
    from_email: str | None = None,
    to_emails: str | None = None,
    channel: str | None = "email",
    direction: str | None = None,
    source: str = "manual",
    occurred_at: datetime | None = None,
    classify: bool = True,
    created_by_id: UUID | None = None,
) -> ProjectCommunication:
    """Persist + classify + extract-trades for one communication. Returns the row."""
    text_body = (body or "").strip()
    fields: dict[str, Any] = {
        "category": None, "summary": None, "action_items": [], "urgency": None,
        "direction": direction, "has_quote": False, "trades": [],
    }
    processed_at = None
    if classify and text_body:
        try:
            from app.services.communication_classify import classify_communication
            ai = classify_communication(subject, text_body, from_email)
            fields.update(ai)
            if direction:  # caller-set direction wins
                fields["direction"] = direction
            processed_at = datetime.now(timezone.utc)
        except Exception:  # noqa: BLE001 — classification is best-effort
            pass

    c = ProjectCommunication(
        project_id=project_id, source=source, channel=channel,
        direction=fields["direction"], from_name=from_name, from_email=from_email,
        to_emails=to_emails, subject=subject, body=text_body,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        category=fields["category"], summary=fields["summary"],
        action_items=fields["action_items"], urgency=fields["urgency"],
        has_quote=fields["has_quote"], processed_at=processed_at, created_by_id=created_by_id,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    extract_trades(db, fields.get("trades"), c)
    return c
