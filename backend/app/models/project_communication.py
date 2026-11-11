from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


# Construction communication taxonomy (from industry research). Used by the
# AI classifier and the Conversations UI filters.
COMMUNICATION_CATEGORIES = (
    "general",            # day-to-day admin / correspondence
    "rfi",                # request for information / clarification
    "selection",          # finishes/fixtures/material selections & approvals
    "quote",              # trade/supplier quote or bid
    "change_order",       # change request / change order
    "schedule",           # timeline / progress / look-ahead
    "financial",          # billing / draw / payment
    "deficiency",         # defect / punch-list / issue
    "permit_inspection",  # permits / inspections / authority
    "meeting",            # meeting minutes / decisions
    "warranty",           # warranty / service / post-completion
    "other",
)
COMMUNICATION_SOURCES = ("manual", "bcc", "forward")
COMMUNICATION_URGENCY = ("low", "normal", "high")


class ProjectCommunication(UUIDPKMixin, TimestampMixin, Base):
    """One logged communication (email / message / call / note) on a project."""

    __tablename__ = "project_communications"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False, default="manual", server_default="manual")
    direction: Mapped[str | None] = mapped_column(Text)   # inbound | outbound | internal
    channel: Mapped[str | None] = mapped_column(Text)     # email | sms | call | meeting | note
    from_name: Mapped[str | None] = mapped_column(Text)
    from_email: Mapped[str | None] = mapped_column(Text)
    to_emails: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # AI classification + extraction.
    category: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    action_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    urgency: Mapped[str | None] = mapped_column(Text)
    has_quote: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
