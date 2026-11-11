from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


PROJECT_STATUSES = ("lead", "preconstruction", "in_progress", "closeout", "archived")


class Project(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    # Short client-facing project description, shown on the package overview page.
    summary: Mapped[str | None] = mapped_column(Text)
    # Site coordinates, lazily geocoded from `address` (Nominatim) the first
    # time the Hours view loads. Used to flag punches recorded off-site.
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6))
    geocoded_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    type: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Enum(*PROJECT_STATUSES, name="project_status"),
        nullable=False,
        default="lead",
        server_default="lead",
        index=True,
    )
    phase: Mapped[str | None] = mapped_column(Text)
    # The status the project held immediately before it was archived, so
    # Unarchive can restore it exactly. NULL when the project isn't archived.
    pre_archive_status: Mapped[str | None] = mapped_column(Text)
    # Per-project client-discussion notes for the package's "What We Discussed"
    # section. List of {title, body, computed?}. Editable in the UI; can be
    # AI-drafted. Replaces the old hard-coded 1234 fallback.
    discussion_points: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    client_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"), index=True,
    )
    building_age: Mapped[int | None] = mapped_column(Integer)
    # builder_grade | mid | high. Runs through flooring, millwork and fixtures
    # together. NULL = not decided; the estimate states the assumption it made.
    finish_level: Mapped[str | None] = mapped_column(Text)
    # finished | shell | part_shell. A shell leaves whole divisions unpriced.
    delivered_as: Mapped[str | None] = mapped_column(Text)
    # Migration 0069. The finish, asked question by question and confirmed by
    # a person: {key: {answer, suggested, why, confirmed, confirmed_at, ...}}.
    # Replaces asking for `finish_level` (kept, no longer asked).
    finish_selections: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # delivered_as = part_shell: which areas are left unfinished, in words.
    part_shell_note: Mapped[str | None] = mapped_column(Text)
    # What was decided about a discipline with no drawings, keyed by discipline
    # ("all" when there are no drawings at all): "allowance" | "not_in_scope".
    drawing_decisions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    building_type: Mapped[str | None] = mapped_column(Text)
    sqft: Mapped[int | None] = mapped_column(Integer)
    # Phase 7-style nav redesign — bookmark to the source Drive folder. Files
    # themselves are still uploaded into R2 manually; this is just the URL.
    google_drive_url: Mapped[str | None] = mapped_column(Text)
    # When false, the document-indexer auto-trigger skips this project.
    # Useful for historical / archive dumps that shouldn't burn AI cost
    # on every upload. Defaults true on new projects.
    index_documents: Mapped[bool] = mapped_column(
        "index_documents", default=True, nullable=False, server_default="true"
    )
    # Fingerprint of the client package as last sent to the client:
    # {sections:{<name>:<hash>}, marked_at, marked_by}. NULL until first sent.
    # Compared against the live fingerprint to warn when the package is stale.
    package_baseline: Mapped[dict | None] = mapped_column(JSONB)
    # True when this is a "new home" (dwelling) under the BC Homeowner Protection
    # Act — houses, laneway houses, secondary suites. Drives the 2-5-10 home
    # warranty disclosure in the contract. False for accessory buildings.
    home_warranty_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Opaque per-project token for the inbound-email BCC address
    # (p-<token>@<inbound_email_domain>). Lazily generated on first reveal.
    inbound_token: Mapped[str | None] = mapped_column(Text, unique=True)
