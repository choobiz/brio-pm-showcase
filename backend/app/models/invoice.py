from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


INVOICE_TYPES = ("progress", "tm", "change_order", "retainer")
INVOICE_STATUSES = ("draft", "sent", "paid", "partial", "overdue", "void")


class Invoice(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "invoices"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    type: Mapped[str] = mapped_column(Enum(*INVOICE_TYPES, name="invoice_type"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(
        Enum(*INVOICE_STATUSES, name="invoice_status"),
        nullable=False,
        default="draft",
        server_default="draft",
    )
    #: Client-supplied, generated once when the create form opens and reused on
    #: retry. Lets a repeated POST return the invoice it already made instead of
    #: taking a second number out of the sequence. Null for every invoice made
    #: before 2026-10-06 and for any created outside the form.
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"), server_default="0")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When it was first sent to the client. Migration 0070.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
