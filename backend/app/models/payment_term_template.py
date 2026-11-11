from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


class PaymentTermTemplate(UUIDPKMixin, TimestampMixin, Base):
    """A reusable payment / draw schedule template. Built-ins are seeded and not
    deletable; users can save their own. ``entries`` = [{milestone, pct, detail}]
    (pcts should sum to 100). Applying a template writes the entries into a
    contract's payload.paymentSchedule, which the contract PDF renders."""

    __tablename__ = "payment_term_templates"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    entries: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
