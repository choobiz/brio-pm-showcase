"""Daily site report — schedule-driven on-site daily log.

One report per (project, calendar day). It is *pre-filled* from the project's
active schedule (the activities whose date range overlaps the day) so the site
supervisor fills in actuals rather than writing from scratch. When the schedule
shifts, the report is reconciled against the new schedule without discarding any
entered data (see services/daily_site_report.py).

`schedule_id` records the schedule version the report was last synced against —
if the active schedule moves past it, the UI offers a re-sync.

The `payload` (JSONB) holds the day's content:
  {
    "conditions": {"weather", "temp_c", "ground"},
    "manpower":   [{"trade", "company", "count", "hours"}],
    "tasks":      [{activity_code, name, phase, scheduled_start, scheduled_finish,
                    is_critical, from_schedule, ad_hoc, removed,
                    status, percent_complete, crew, notes}],
    "deliveries": [{"item", "supplier", "notes"}],
    "visitors":   [{"name", "company", "purpose"}],
    "safety":     {"incident": bool, "notes"},
    "delays":     [{"reason", "impact", "notes"}],
    "notes":      "<general narrative>"
  }
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


DAILY_REPORT_STATUSES = ("draft", "submitted", "finalized")
# Per-task status the supervisor sets against a scheduled/ad-hoc task.
DAILY_TASK_STATUSES = ("not_started", "in_progress", "completed", "delayed", "not_worked")


class DailySiteReport(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "daily_site_reports"
    __table_args__ = (
        UniqueConstraint("project_id", "report_date", name="uq_daily_report_project_date"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Schedule version this report was last synced against (drift detection).
    # No ORM-level ForeignKey — `schedules` is a raw (unmapped) table, so the FK
    # is enforced at the DB level (migration 0028) only.
    schedule_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # draft | submitted | finalized
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="draft", server_default="draft"
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
