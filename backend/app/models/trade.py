from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


# Cross-project trade / subcontractor / supplier directory categories.
# Ordered roughly by construction sequence, suppliers/professionals last.
# The shared trade list (services/library_vocab.TRADES), so the directory,
# rates and assemblies name a trade the same way (plan phase 1, 2026-10-15).
# Every key the directory used before is still in it.
from app.services.library_vocab import TRADE_KEYS as TRADE_CATEGORIES  # noqa: E402
TRADE_STATUSES = ("unconfirmed", "active", "archived")
TRADE_SOURCES = ("manual", "communication")


class Trade(UUIDPKMixin, TimestampMixin, Base):
    """A trade / subcontractor / supplier in the cross-project directory.

    Deliberately NOT project-scoped — the library spans every project (a plumber
    used on one job should surface on the next). Dedup on create/extract follows
    email > phone > normalized company/contact name, mirroring the Client
    find-or-create priority in ``services/clients``.

    AI-extracted rows (``source='communication'``) land as ``unconfirmed`` so
    email-signature noise never silently pollutes the active directory; the
    office confirms them with one click.
    """

    __tablename__ = "trades"

    company_name: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default="", index=True,
    )
    trade_category: Mapped[str] = mapped_column(
        Text, nullable=False, default="other", server_default="other", index=True,
    )

    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    emails: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )
    normalized_emails: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )
    phones: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )
    normalized_phones: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )

    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )
    notes: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[int | None] = mapped_column(Integer)  # 1-5, optional

    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default="active", index=True,
    )
    source: Mapped[str] = mapped_column(
        Text, nullable=False, default="manual", server_default="manual",
    )
    # Provenance when auto-extracted from a Conversations entry.
    source_communication_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("project_communications.id", ondelete="SET NULL"),
    )
    # The project whose communication first surfaced this trade (context only).
    first_project_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"),
    )
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
