"""Submittal review: a shop drawing, product data or quote checked against the
project's governing documents before anything is ordered or built.

Wireframes approved by Amir 2026-10-17 (Documents › Submittals; the standard
four outcomes; "cleared to order" is a marker only; the first five trade
checklists; a revision is checked against the one before it).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin

#: What goes back to the supplier: the usual four review stamps.
OUTCOMES = ("no_exceptions", "as_noted", "revise_resubmit", "rejected")
#: draft → reviewing → needs_call → waiting → closed; review_failed when the run
#: came back unusable; superseded when a newer revision of it is reviewed.
STATUSES = ("draft", "reviewing", "review_failed", "needs_call", "waiting", "closed", "superseded")


class Submittal(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "submittals"

    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
                                             nullable=False, index=True)
    #: S1, S2… per project; a revision keeps its parent's ref.
    ref: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    trade: Mapped[str] = mapped_column(Text, nullable=False)
    supplier: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[str] = mapped_column(Text, nullable=False, default="REV 0", server_default="REV 0")
    #: The revision this one replaces, whose findings it is checked against.
    parent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True),
                                                   ForeignKey("submittals.id", ondelete="SET NULL"))
    submittal_doc_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    governing_doc_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    instructions: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft", server_default="draft")
    error: Mapped[str | None] = mapped_column(Text)
    ai_job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    approved_usd: Mapped[float | None] = mapped_column(Numeric(10, 4))
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 4))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)
    #: [{id, check_key, check, shop, shop_ref, plan, plan_ref, result, why,
    #:   ask_who, question, call, call_note, previous}]
    findings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    #: {supplier, number, date, subtotal, tax, total, bom_matches, notes[]} or {}
    quote: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    outcome: Mapped[str | None] = mapped_column(Text)
    outcome_note: Mapped[str | None] = mapped_column(Text)
    outcome_by: Mapped[str | None] = mapped_column(Text)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The engineer's signed review is in (one of the three ticks before ordering).
    signed_received: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    milestone_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True),
                                                      ForeignKey("project_milestones.id", ondelete="SET NULL"))
    #: Saved question emails, one per party: [{party, to_name, to_email, subject, body, copied_at}]
    emails: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    #: Inbound replies filed by their [KS-Sn] tag.
    thread: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    #: [{at, by, text}] — the trail on screen D.
    history: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="system", server_default="system")
