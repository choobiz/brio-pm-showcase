"""Which of a project's documents feed the scope, and which feed quantities.

See migration 0051 for why this exists: filing category was being used as the
gate on what the AI reads, and it is a librarian's label rather than a decision
about what to price from.

A row exists only for a document someone has touched. Absence means "not
selected", so the table stays small on projects with 4,000 documents and a
handful of relevant ones.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DocumentSelection(Base):
    __tablename__ = "document_selections"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    use_for_scope: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    use_for_quantities: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    updated_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
