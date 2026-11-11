from datetime import date, datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


class Document(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    category: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    r2_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str | None] = mapped_column(Text)
    # NULL until the client finishes the R2 upload and calls finalize.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    uploaded_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    # Soft delete — Track B never hard-deletes (R2 object + audit trail survive).
    archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    # When true, this document/photo appears in the client's owner portal.
    # Default false so nothing leaks; managers share explicitly.
    client_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )

    # ── drawing revisions (migration 0057) ──────────────────────────────────
    # What a PERSON confirmed, never what the filename parser guessed.
    # services/drawing_register proposes; these hold what was accepted.
    #
    # ⚠️ There is deliberately no `is_current` column. Current is DERIVED —
    # within a group, the non-duplicate member with the latest issue date and
    # highest revision. Storing it would be a second copy of a fact that can
    # drift, and would need rewriting across the whole group whenever one file
    # is added.

    #: Which drawing this file is a version of. Revisions share it. Not a
    #: foreign key: the group has no existence beyond the identity asserted.
    drawing_group_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    #: "R3", "Rev B", "PRELIM" — as confirmed, not necessarily as parsed.
    revision_label: Mapped[str | None] = mapped_column(Text)
    #: Primary sort for which revision is in force: a drawing issued in
    #: November supersedes one issued in October whatever the labels say.
    issued_on: Mapped[date | None] = mapped_column(Date)
    #: This file IS another file — a re-upload, a "Copy of", or the same
    #: drawing exported to another CAD format. Distinct from an earlier
    #: revision, and a duplicate never contributes one.
    duplicate_of_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"),
    )
    #: Without these the register cannot tell "reviewed, and it is one version"
    #: from "never looked at" — the same distinction the quantity gate draws.
    register_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    register_reviewed_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )

    # ── category confirmation (migration 0067) ──────────────────────────────
    #: When a person said "this category is right". NULL on every row that
    #: existed before 0067: imports and the filename guesser wrote `category`,
    #: so nobody can be said to have confirmed those.
    category_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    category_confirmed_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
