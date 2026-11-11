"""What actually happened to a schedule activity: typed-in facts.

Keyed by (project, activity_code), NOT by schedule version: every new version
copies its activities, so a date stored on an activity row would split across
versions or be lost on the next re-plan. What happened on site does not change
when the plan does.

Only manual entries are stored here. Facts read from daily site reports are
derived on every read (services/actuals.py), so they are never stale; a field
set here overrides the derived value for that field.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin

#: done | started | not_started
ACTUAL_STATUSES = ("done", "started", "not_started")


class ActivityActual(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "activity_actuals"
    __table_args__ = (
        UniqueConstraint("project_id", "activity_code", name="uq_activity_actual_project_code"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    activity_code: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(Text)
    actual_start: Mapped[date | None] = mapped_column(Date)
    actual_finish: Mapped[date | None] = mapped_column(Date)
    percent_complete: Mapped[int | None] = mapped_column(Integer)
    #: Why it is late or not started — the reason a re-plan will quote.
    reason: Mapped[str | None] = mapped_column(Text)
    recorded_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
