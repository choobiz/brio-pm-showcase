"""A supervisor's walkthrough of one area against a list of checks.

⚠️ NOT an authority inspection — see `inspections` for those, and migration
0064 for why the two cannot be the same record. And ⚠️ not a code-compliance
instrument: a passing checklist says our supervisor looked and was satisfied.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin

QA_STATUSES = ("open", "complete")

#: null is a real value and the most important one: not yet looked at.
QA_RESULTS = ("pass", "fail", "na")


class QAChecklist(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "qa_checklists"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="open", server_default="open",
    )
    items: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    performed_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
