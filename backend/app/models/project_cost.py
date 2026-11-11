from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


# A cost is something BRIO *received* — distinct from `invoices`, which are
# what BRIO *issues* to clients.
PROJECT_COST_KINDS = ("subtrade_invoice", "supplier_invoice", "subtrade_quote", "other")


class ProjectCost(UUIDPKMixin, TimestampMixin, Base):
    """A cost BRIO incurred on a project — a subtrade/supplier invoice, or a
    quote a subtrade sent BRIO.

    This is the "actuals" side of estimate-vs-actual analysis. It is kept
    separate from `invoices` (BRIO -> client) so the two money directions are
    never conflated. Populated by the Drive migration's structured extraction.
    """

    __tablename__ = "project_costs"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # The migrated document this cost was extracted from. SET NULL on delete.
    source_document_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), index=True,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="other", server_default="other")
    vendor: Mapped[str | None] = mapped_column(Text)
    # One of the 18 standard cost categories, when determinable.
    category: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    cost_date: Mapped[date | None] = mapped_column(Date)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # Entered on Money › Costs (migration 0084). `amount` is BEFORE GST; `category`
    # holds the project's division label. A Drive-migration row has these empty.
    gst: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    invoice_number: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )


class ProjectCostBudget(UUIDPKMixin, TimestampMixin, Base):
    """Budget for one division of a project, to compare bills against.

    Several rows can share a division: the contract's share (spread by an
    estimate's proportions) and each approved change order stay separate, so
    the screen can say where each dollar of budget came from.
    """

    __tablename__ = "project_cost_budgets"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    division: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
