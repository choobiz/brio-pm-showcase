from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UUIDPKMixin


class Narrative(UUIDPKMixin, Base):
    """A written note about a project — the reasoning, not the numbers.

    Estimates, schedules and takeoffs hold what the figures are; this holds why
    they are what they are, and what the site has been told. Markdown so it can
    be read, diffed and pasted into an email without a renderer.

    ⚠️ No `TimestampMixin`: the table has `created_at` and deliberately no
    `updated_at`, because a row is never updated. A revision is a NEW row
    pointing back through `parent_version_id`, so last week's version survives
    exactly as it was issued.
    """

    __tablename__ = "narratives"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    #: What the note is about — 'projects' for project-wide context, else a table
    #: name ('schedules', 'cash_flows', 'drawing_analyses'). Deliberately NOT a
    #: foreign key: a note about a superseded schedule must outlive it.
    parent_table: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    title: Mapped[str] = mapped_column(Text, nullable=False)
    #: Images are referenced as `![alt](doc:<document-uuid>)`, never by URL — an
    #: R2 presign expires in minutes and this content is kept for years.
    content_md: Mapped[str] = mapped_column(Text, nullable=False)

    version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("narratives.id", ondelete="SET NULL"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
