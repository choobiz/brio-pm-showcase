"""Text read out of a document, page by page — see migration 0068."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean, CheckConstraint, Computed, DateTime, ForeignKey, Index, Integer, Text, func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

TEXT_STATUSES = ("pending", "running", "ok", "no_text", "too_large", "failed")


class DocumentText(Base):
    __tablename__ = "document_texts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','ok','no_text','too_large','failed')",
            name="ck_document_texts_status"),
    )

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    #: pending → running → ok | no_text | too_large | failed
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending",
                                        server_default="pending", index=True)
    page_count: Mapped[int | None] = mapped_column(Integer)
    pages_with_text: Mapped[int | None] = mapped_column(Integer)
    char_count: Mapped[int | None] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                            server_default="false")
    #: Why there is no text, in words the Documents page can show as-is.
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now(), onupdate=func.now())


class DocumentTextPage(Base):
    __tablename__ = "document_text_pages"
    __table_args__ = (
        Index("ix_document_text_pages_tsv", "tsv", postgresql_using="gin"),
    )

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True,
    )
    #: 1-based, as a person reads it. A Word file or spreadsheet is page 1.
    page_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tsv = mapped_column(TSVECTOR, Computed("to_tsvector('english', text)", persisted=True))
