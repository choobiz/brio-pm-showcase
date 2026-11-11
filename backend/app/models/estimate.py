from uuid import UUID

from sqlalchemy import Enum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


ESTIMATE_DETAIL_LEVELS = ("summary", "standard", "detailed", "fully_itemized")
ESTIMATE_STATUSES = ("draft", "sent", "accepted", "rejected", "expired")


class Estimate(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "estimates"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    ref: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Per-project version; highest version is the active estimate for invoicing.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    detail_level: Mapped[str] = mapped_column(
        Enum(*ESTIMATE_DETAIL_LEVELS, name="estimate_detail"),
        nullable=False,
        default="standard",
        server_default="standard",
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(
        Enum(*ESTIMATE_STATUSES, name="estimate_status"),
        nullable=False,
        default="draft",
        server_default="draft",
    )
    # The estimate_generation AIJob that produced this estimate. NULL for
    # hand-created estimates. SET NULL on job delete.
    ai_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ai_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
