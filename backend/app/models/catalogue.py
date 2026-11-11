"""Starter library (rate libraries plan, phase 2, 2026-10-16).

Two read-only reference tables, deliberately separate from ``material_rates``:
every active material row is listed in the estimate prompt
(services/rate_reference.py), so 7,912 supplier lines there would bury the
curated rates the estimator is meant to use. The catalogue is looked up, never
prompted.

- :class:`CatalogueItem` — a supplier price-list line as published.
- :class:`CostIndexPoint` — one quarter of StatCan's building construction price
  index, used to say how far a stored price has likely moved since it was
  entered. Nothing here ever changes a price.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


class CatalogueItem(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "catalogue_items"
    __table_args__ = (UniqueConstraint("supplier", "sku", name="uq_catalogue_items_supplier_sku"),)

    supplier: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    #: The supplier's own filing ("Drywall & Steel Framing" › "Tape and Bead").
    supplier_group: Mapped[str | None] = mapped_column(Text)
    supplier_category: Mapped[str | None] = mapped_column(Text)
    #: As printed (SHT, BDL, MSF ...) and the price printed against it.
    raw_unit: Mapped[str] = mapped_column(Text, nullable=False)
    raw_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    #: Canonical unit and the price per that unit; null when the printed unit
    #: has no safe reading (YARD: linear or square?), never guessed.
    unit: Mapped[str | None] = mapped_column(Text)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    trade_key: Mapped[str | None] = mapped_column(Text, index=True)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    source_ref: Mapped[str | None] = mapped_column(Text)


class CostIndexPoint(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "cost_index_points"
    __table_args__ = (UniqueConstraint("series", "division", "ref_period",
                                       name="uq_cost_index_points_series_div_period"),)

    #: e.g. "statcan-18100289:Vancouver:Single-detached house"
    series: Mapped[str] = mapped_column(Text, nullable=False)
    division: Mapped[str] = mapped_column(Text, nullable=False)
    #: First day of the quarter the value describes.
    ref_period: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class CatalogueImport(UUIDPKMixin, TimestampMixin, Base):
    """One supplier price sheet, from upload to applied (or undone).

    Phase 4 (approved 2026-10-16): a sheet is parsed and checked into a
    "staged" import first; nothing touches the catalogue or the library until
    it is applied, and an applied import can be undone from `changes`."""

    __tablename__ = "catalogue_imports"

    supplier: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text)
    #: staged | applied | undone | discarded
    status: Mapped[str] = mapped_column(Text, nullable=False, default="staged", server_default="staged")
    column_map: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    #: The parsed, checked rows (staged) — kept so apply needs no re-upload.
    rows: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    #: What apply did, row by row, so undo can put every value back:
    #: [{target: catalogue|material, id, action: added|updated, before: {...}}]
    changes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    applied_by: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_by: Mapped[str | None] = mapped_column(Text)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
