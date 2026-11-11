"""Employee time entry — a punch-in / punch-out shift on a project.

Each entry ties to the logged-in employee (user_id) and a project. Punch-in and
punch-out each capture a GPS location (lat/lng/accuracy) for anti-cheat review.
A partial unique index enforces ONE open (un-punched-out) entry per user across
all projects, so an employee can't be clocked into two jobs at once.
Hours are computed from the timestamps at read time (exact, no rounding).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


TIME_ENTRY_STATUSES = ("open", "closed")


class TimeEntry(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "time_entries"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    punch_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    punch_in_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    punch_in_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    punch_in_accuracy: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))  # metres
    punch_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    punch_out_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    punch_out_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    punch_out_accuracy: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    # open | closed
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="open", server_default="open"
    )
    notes: Mapped[str | None] = mapped_column(Text)
    # field (from the punch clock) | manual (office correction)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, default="field", server_default="field"
    )
