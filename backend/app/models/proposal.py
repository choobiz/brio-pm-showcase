"""A change somebody suggested, and what was decided about it.

See alembic/versions/0061_proposals.py for why a rejected proposal is kept
permanently: "we considered that and chose not to" is the answer a dispute
turns on more often than "we did it".
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin

#: pending  — waiting for a person
#: accepted — applied, with `revision_id` pointing at the snapshot taken first
#: rejected — declined, with a reason that is REQUIRED
#: stale    — the record moved underneath it; it can no longer be applied safely
#: superseded — a later proposal replaced it before anyone decided
PROPOSAL_STATUSES = ("pending", "accepted", "rejected", "stale", "superseded")

#: Statuses that are final. A decided proposal is never re-opened — a new
#: proposal is made instead, so the history reads as a sequence of decisions
#: rather than one row that changed its mind.
TERMINAL_STATUSES = ("accepted", "rejected", "superseded")


class Proposal(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "proposals"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    #: Polymorphic target. Checked against an allow-list in the service — a
    #: free-text table name that reached SQL would be an injection surface.
    target_table: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)

    origin: Mapped[str] = mapped_column(Text, nullable=False)
    ai_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="SET NULL"),
    )
    proposed_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    change_set: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    impact: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )

    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending",
    )
    decided_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)

    #: The snapshot taken before applying, so an accepted proposal is
    #: restorable. A revision NUMBER — `revisions.snapshot()` returns an int and
    #: a revision is identified by (table, row_id, number).
    revision_number: Mapped[int | None] = mapped_column(Integer)
