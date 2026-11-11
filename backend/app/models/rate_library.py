"""BRIO rate library — installed unit rates for deterministic estimate pricing.

Track 2 of the estimate-workflow redesign. BRIO is a general contractor that
subcontracts most trades **lump-sum**, so the primary rate the estimator needs
is an *installed* unit rate, not a material + labour build-up. Installed rates
are sourced, in priority order:

  1. ``subcontract_derived`` — a past subtrade lump-sum (a :class:`ProjectCost`)
     divided by the CAD-takeoff quantity for that project → a composite
     installed unit rate. This is "our rate": what BRIO actually paid, unitized.
     Both inputs already live in the system (project_costs + quantity_overrides).
  2. ``published`` — RSMeans / Hanscomb Yardsticks, localized to Vancouver
     (reference / calibration; the only two products that publish Canadian
     per-assembly installed unit rates with a labour/material split).
  3. ``composed`` — ``material_rate`` + ``labour_rate`` × productivity, for the
     trades BRIO self-performs. The material_rates / labour_rates tables below
     feed this path; productivity is back-solved from a known installed price.
  4. ``market`` — industry fallback, always tagged as such so it is never
     mistaken for our own pricing.

Everything is **project-type aware** (residential vs commercial) because BC
subtrade and labour pricing differ materially between the two.

The estimator picks the best active :class:`RateEntry` per assembly by
source-priority then recency; the chosen entry's id + value are stamped into the
estimate payload provenance (Track 1), so a generated estimate always records
which rate produced each number — no separate snapshot table required.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


# Where an installed rate came from, in trust order (index 0 = most trusted).
RATE_SOURCE_TYPES = ("own", "subcontract_derived", "published", "composed", "market")
# How firm the number is — surfaced to admin alongside the price.
RATE_CONFIDENCE = ("measured", "derived", "reference", "fallback")
# Rates differ between residential and commercial; "any" = applies to both.
RATE_PROJECT_TYPES = ("residential", "commercial", "any")
# Where a material or labour input came from.
MATERIAL_SOURCES = ("own", "supplier", "market")   # own = set in estimate review; supplier = our submitted rate
LABOUR_SOURCES = ("own", "association", "workbc", "market")  # own = our rate


class RateAssembly(UUIDPKMixin, TimestampMixin, Base):
    """A priceable unit of work — the stable catalog the estimator prices
    against (e.g. ``FRAME-EXT-2X6-WALL`` measured per sqft of wall). One row per
    assembly; the actual prices live in :class:`RateEntry` (many per assembly,
    across sources and dates)."""

    __tablename__ = "rate_assemblies"

    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Sub-trade the assembly belongs to: framing, drywall, ...
    trade: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    trade_key: Mapped[str | None] = mapped_column(Text, index=True)
    # Maps to one of the standard estimate categories, when applicable.
    category: Mapped[str | None] = mapped_column(Text)
    # Unit the rate is expressed in: sqft, lf, ea, cy, sf_floor, ls, ...
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    project_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="any", server_default="any"
    )
    description: Mapped[str | None] = mapped_column(Text)
    # What the rate is defined to include / exclude — critical for lump-sum
    # derivation, where the quantity must match the subcontract scope.
    scope_notes: Mapped[str | None] = mapped_column(Text)
    # active | retired
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default="active"
    )
    owner_namespace: Mapped[str] = mapped_column(
        Text, nullable=False, default="brio-pm", server_default="brio-pm"
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="system", server_default="system"
    )


class RateEntry(UUIDPKMixin, TimestampMixin, Base):
    """One priced observation of an assembly's installed unit rate, tagged with
    its source and (for derived rates) the exact lump-sum + quantity it came
    from. Multiple entries per assembly accumulate over time; the estimator
    selects the best active one by source-priority then recency."""

    __tablename__ = "rate_entries"

    assembly_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("rate_assemblies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Installed price per assembly unit, in CAD.
    unit_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Snapshot of the assembly unit at entry time (guards against later edits).
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)  # RATE_SOURCE_TYPES
    confidence: Mapped[str] = mapped_column(
        Text, nullable=False, default="derived", server_default="derived"
    )
    project_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="any", server_default="any"
    )
    # For subcontract_derived: the project + cost the lump-sum came from.
    source_project_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    source_cost_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("project_costs.id", ondelete="SET NULL")
    )
    # Human-readable citation: "RSMeans 09 29 00 (Vancouver ×0.94)",
    # "Renovo supplier quote 2026-07", "WorkBC carpenters high ×0.80", etc.
    source_ref: Mapped[str | None] = mapped_column(Text)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    superseded_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("rate_entries.id", ondelete="SET NULL")
    )
    # Derivation detail so any rate is auditable, e.g. for subcontract_derived:
    # {lump_sum, quantity, quantity_source, waste_pct}; for composed:
    # {material, labour_rate, hours, material_source, labour_source}.
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="system", server_default="system"
    )
    notes: Mapped[str | None] = mapped_column(Text)


class MaterialRate(UUIDPKMixin, TimestampMixin, Base):
    """A supply-only material cost. ``source='supplier'`` rows are BRIO's own
    submitted rates (primary); ``source='market'`` rows are the researched
    fallback used when we have no supplier rate for an item. Feeds the
    ``composed`` installed-rate path and any material-only line items."""

    __tablename__ = "material_rates"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    sku: Mapped[str | None] = mapped_column(Text)
    trade: Mapped[str | None] = mapped_column(Text, index=True)
    category: Mapped[str | None] = mapped_column(Text)
    trade_key: Mapped[str | None] = mapped_column(Text, index=True)
    # ea, sheet, bf, lf, sqft, kg, box, ...
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # MATERIAL_SOURCES
    supplier: Mapped[str | None] = mapped_column(Text)
    project_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="any", server_default="any"
    )
    # Default waste allowance for this material (e.g. 10 for drywall).
    waste_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    #: What the takeoff measures it in (sf, lf, ea …) and how many of those one
    #: purchase `unit` covers: a 4x8 sheet covers 32 sf. Null when the material
    #: is already bought by a measure unit, or nobody has set it yet.
    measure_unit: Mapped[str | None] = mapped_column(Text)
    coverage: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    as_of_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    superseded_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("material_rates.id", ondelete="SET NULL")
    )
    source_ref: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="system", server_default="system"
    )


class LabourRate(UUIDPKMixin, TimestampMixin, Base):
    """An hourly labour rate for the ``composed`` path. ``source='own'`` rows are
    BRIO's own rates (primary). Fallback rows come from an association agreement
    or WorkBC/Job Bank and are stored AS POSTED. ``discount_pct`` defaults to 0 and
    is set only for a genuine discount; ``effective_rate`` = base × (1 − discount/100)."""

    __tablename__ = "labour_rates"

    # framer, drywaller, ...
    trade: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    #: The shared trade key (services/library_vocab.TRADES); `trade` keeps the
    #: occupation as published ("plumber-apprentice").
    trade_key: Mapped[str | None] = mapped_column(Text, index=True)
    region: Mapped[str] = mapped_column(
        Text, nullable=False, default="metro-vancouver", server_default="metro-vancouver"
    )
    project_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="any", server_default="any"
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)  # LABOUR_SOURCES
    # Published / association / own base rate before any discount.
    base_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    # Optional, and 0 unless a real discount exists (e.g. a negotiated
    # association rate). Published wage data is stored as posted.
    discount_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    # base_rate × (1 − discount_pct/100), or base_rate for source='own'.
    effective_rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    superseded_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("labour_rates.id", ondelete="SET NULL")
    )
    source_ref: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="system", server_default="system"
    )
