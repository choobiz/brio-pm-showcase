from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


CHANGE_ORDER_STATUSES = ("draft", "pending", "approved", "rejected", "void")


class ChangeOrder(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "change_orders"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)  # sequential per project → "CO-<number>"
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Pre-tax cost impact (+ = addition, − = credit). GST applied for client display.
    cost_impact: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"), server_default="0")
    schedule_impact_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft", server_default="draft")
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    #: Which AI run drafted this, or NULL when it was not recorded.
    #:
    #: ⚠️ NULL IS "NOT RECORDED", NOT "WRITTEN BY A PERSON". Every row created
    #: before 2026-10-02 is NULL because the link was never captured. The
    #: `detail.source_job_id` key carried it inside JSONB, which is not
    #: queryable and so could not answer "which of these did the model draft".
    ai_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="SET NULL"),
    )
    decided_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When it went to the client (draft → pending). Migration 0070.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_note: Mapped[str | None] = mapped_column(Text)
    # Line items, assumptions and exclusions behind cost_impact. Kept on the
    # change order rather than on the AI job that produced it: the working must
    # outlive the job row, and a hand-written change order can carry a breakdown
    # too.
    detail: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
