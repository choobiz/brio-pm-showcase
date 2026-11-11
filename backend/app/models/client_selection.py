from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


# 'select' → the client chooses one of `options`; 'approve' → the client approves
# a single proposed item or requests changes.
SELECTION_DECISION_TYPES = ("select", "approve")
# 'pending' → awaiting the client; 'submitted' → client decided; 'confirmed' →
# the office has locked the decision in.
SELECTION_STATUSES = ("pending", "submitted", "confirmed")
# Who buys the material: BRIO's trades or the client (e.g. to earn points).
SELECTION_PURCHASERS = ("brio", "client")


class ClientSelection(UUIDPKMixin, TimestampMixin, Base):
    """A material/finish selection or approval the client makes in the portal."""

    __tablename__ = "client_selections"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    decision_type: Mapped[str] = mapped_column(Text, nullable=False, default="select", server_default="select")
    purchaser: Mapped[str | None] = mapped_column(Text)
    # [{id, label, description, price_delta}] — the choices offered to the client.
    options: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending")
    # For 'select': the chosen option id. For 'approve': 'approved' | 'changes_requested'.
    selected_option_id: Mapped[str | None] = mapped_column(Text)
    client_comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    # Round 2 (R2Selections, 2026-11-02): planned against the schedule.
    activity_code: Mapped[str | None] = mapped_column(Text)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    allowance: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("change_orders.id", ondelete="SET NULL"))
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
