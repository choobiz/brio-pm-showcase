from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


class DocumentSummary(UUIDPKMixin, TimestampMixin, Base):
    """One row per indexed document. Source of truth for the project-level
    MD index — the MD is rendered from these rows on demand and cached as
    a `project_index` Document row in R2."""

    __tablename__ = "document_summaries"

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="document_summary@1"
    )
    # queued | running | succeeded | failed | skipped
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", index=True
    )
    summary_text: Mapped[str | None] = mapped_column(Text)
    structured_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    raw_extracted_text: Mapped[str | None] = mapped_column(Text)
    ai_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="SET NULL")
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
