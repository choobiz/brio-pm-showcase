"""rate_assemblies + rate_entries + material_rates + labour_rates — Track 2 P5.

BRIO rate library. Installed-rate-centric because BRIO subcontracts lump-sum:
the primary "our rate" is a past subtrade lump-sum ÷ CAD-takeoff quantity
(subcontract_derived), with published (RSMeans/Hanscomb), composed
(material+labour×productivity, self-perform), and market (fallback) behind it.

Seeds:
  - market material fallback for framing + drywall (researched Metro-Van
    2025–26 supply prices);
  - WorkBC 2025 journeyman labour rates (carpenter, drywaller) × 0.80;
  - two pilot **composed** assemblies (framing package, drywall hang+tape+L4)
    whose installed rate is calibrated to the publicly-cited market installed
    midpoint (a naive journeyman × textbook-productivity build-up overshoots
    market by ~40%), with the full WorkBC-labour + our-material derivation kept
    in the entry payload. Confidence = 'reference'; overwritten by real
    subcontract_derived rates as jobs close.

Revision ID: 0025_rate_library
Revises: 0024_prior_client_estimate
Create Date: 2026-07-12
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


revision = "0025_rate_library"
down_revision = "0024_prior_client_estimate"
branch_labels = None
depends_on = None


# Researched Metro-Vancouver 2025–26 supply-only material prices. These are the
# *fallback* used only when BRIO has no submitted supplier rate for an item.
# name, sku, trade, unit, unit_cost, waste_pct, source_ref
_MARKET_MATERIAL_SEED = [
    # ── Framing ──
    ("2x4 SPF #2 KD", None, "framing", "bf", "0.80", "10", "Metro-Van yard 2025–26 (NRCan SPF)"),
    ("2x6 SPF #2 KD", None, "framing", "bf", "0.95", "10", "Metro-Van yard 2025–26 (NRCan SPF)"),
    ("1/2\" plywood sheathing (4x8)", None, "framing", "sheet", "48.00", "5", "Metro-Van yard 2025–26"),
    ("1/2\" OSB sheathing (4x8)", None, "framing", "sheet", "26.00", "5", "Metro-Van yard 2025–26"),
    ("5/8\" plywood sheathing (4x8)", None, "framing", "sheet", "57.00", "5", "Metro-Van yard 2025–26"),
    # ── Drywall ──
    ("1/2\" gypsum board (4x8)", None, "drywall", "sheet", "21.00", "10", "Metro-Van yard 2025–26"),
    ("5/8\" Type-X gypsum board (4x8)", None, "drywall", "sheet", "28.00", "10", "Metro-Van yard 2025–26"),
    ("Joint compound (all-purpose)", None, "drywall", "kg", "1.50", "0", "Metro-Van yard 2025–26"),
    ("Drywall tape (paper, 75–150m roll)", None, "drywall", "roll", "6.00", "0", "Metro-Van yard 2025–26"),
    ("Drywall screws (1000-ct box)", None, "drywall", "box", "23.00", "0", "Metro-Van yard 2025–26"),
]


def upgrade() -> None:
    # ── rate_assemblies — the priceable catalog ────────────────────────
    op.create_table(
        "rate_assemblies",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("trade", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("project_type", sa.Text, nullable=False, server_default="any"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("scope_notes", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("owner_namespace", sa.Text, nullable=False, server_default="brio-pm"),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Text, nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_rate_assemblies_code"),
    )
    op.create_index("ix_rate_assemblies_trade", "rate_assemblies", ["trade"])

    # ── rate_entries — priced observations (installed unit rates) ───────
    op.create_table(
        "rate_entries",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "assembly_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rate_assemblies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("unit_rate", sa.Numeric(12, 4), nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        # subcontract_derived | published | composed | market
        sa.Column("source_type", sa.Text, nullable=False),
        # measured | derived | reference | fallback
        sa.Column("confidence", sa.Text, nullable=False, server_default="derived"),
        sa.Column("project_type", sa.Text, nullable=False, server_default="any"),
        sa.Column(
            "source_project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_cost_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("project_costs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_ref", sa.Text, nullable=True),
        sa.Column("as_of_date", sa.Date, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "superseded_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rate_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Text, nullable=False, server_default="system"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rate_entries_assembly", "rate_entries", ["assembly_id"])
    op.create_index("ix_rate_entries_project", "rate_entries", ["source_project_id"])
    # Fast "best active rate per assembly" lookups.
    op.create_index(
        "ix_rate_entries_active_lookup",
        "rate_entries",
        ["assembly_id", "project_type", "source_type"],
        postgresql_where=sa.text("is_active = true"),
    )

    # ── material_rates — supplier (our) + market (fallback) ─────────────
    op.create_table(
        "material_rates",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("sku", sa.Text, nullable=True),
        sa.Column("trade", sa.Text, nullable=True),
        sa.Column("category", sa.Text, nullable=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 4), nullable=False),
        # supplier | market
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("supplier", sa.Text, nullable=True),
        sa.Column("project_type", sa.Text, nullable=False, server_default="any"),
        sa.Column("waste_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("as_of_date", sa.Date, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "superseded_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("material_rates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_ref", sa.Text, nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Text, nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_material_rates_trade", "material_rates", ["trade"])

    # ── labour_rates — own (primary) + association/workbc/market ────────
    op.create_table(
        "labour_rates",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trade", sa.Text, nullable=False),
        sa.Column("region", sa.Text, nullable=False, server_default="metro-vancouver"),
        sa.Column("project_type", sa.Text, nullable=False, server_default="any"),
        # own | association | workbc | market
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("base_rate", sa.Numeric(10, 4), nullable=False),
        sa.Column("discount_pct", sa.Numeric(5, 2), nullable=False, server_default="20"),
        sa.Column("effective_rate", sa.Numeric(10, 4), nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "superseded_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("labour_rates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_ref", sa.Text, nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Text, nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_labour_rates_trade", "labour_rates", ["trade"])

    # ── Seed the market material fallback (framing + drywall) ───────────
    material_rates_table = sa.table(
        "material_rates",
        sa.column("name", sa.Text),
        sa.column("sku", sa.Text),
        sa.column("trade", sa.Text),
        sa.column("unit", sa.Text),
        sa.column("unit_cost", sa.Numeric),
        sa.column("source", sa.Text),
        sa.column("project_type", sa.Text),
        sa.column("waste_pct", sa.Numeric),
        sa.column("source_ref", sa.Text),
        sa.column("created_by", sa.Text),
    )
    op.bulk_insert(
        material_rates_table,
        [
            {
                "name": name,
                "sku": sku,
                "trade": trade,
                "unit": unit,
                "unit_cost": unit_cost,
                "source": "market",
                "project_type": "any",
                "waste_pct": waste_pct,
                "source_ref": source_ref,
                "created_by": "alembic-0025",
            }
            for (name, sku, trade, unit, unit_cost, waste_pct, source_ref) in _MARKET_MATERIAL_SEED
        ],
    )

    # ── Seed WorkBC journeyman labour rates × 0.80 (fallback layer) ─────
    # BRIO's own labour rates are primary; these WorkBC-derived rows are the
    # market fallback used when we have no own rate. base_rate = Job Bank BC
    # 2025 "high" (journeyman); effective = base × 0.80.
    labour_rates_table = sa.table(
        "labour_rates",
        sa.column("trade", sa.Text),
        sa.column("region", sa.Text),
        sa.column("project_type", sa.Text),
        sa.column("source", sa.Text),
        sa.column("base_rate", sa.Numeric),
        sa.column("discount_pct", sa.Numeric),
        sa.column("effective_rate", sa.Numeric),
        sa.column("source_ref", sa.Text),
        sa.column("created_by", sa.Text),
    )
    op.bulk_insert(
        labour_rates_table,
        [
            {
                "trade": "framer",
                "region": "metro-vancouver",
                "project_type": "any",
                "source": "workbc",
                "base_rate": "43.71",
                "discount_pct": "20",
                "effective_rate": "34.97",
                "source_ref": "WorkBC/Job Bank NOC 72310 Carpenters BC high (2025) × 0.80",
                "created_by": "alembic-0025",
            },
            {
                "trade": "drywaller",
                "region": "metro-vancouver",
                "project_type": "any",
                "source": "workbc",
                "base_rate": "40.00",
                "discount_pct": "20",
                "effective_rate": "32.00",
                "source_ref": "WorkBC/Job Bank NOC 73102 Drywall/Plasterers BC high (2025) × 0.80",
                "created_by": "alembic-0025",
            },
        ],
    )

    # ── Seed the two pilot assemblies (catalog) ────────────────────────
    assemblies_table = sa.table(
        "rate_assemblies",
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("trade", sa.Text),
        sa.column("category", sa.Text),
        sa.column("unit", sa.Text),
        sa.column("project_type", sa.Text),
        sa.column("description", sa.Text),
        sa.column("scope_notes", sa.Text),
        sa.column("created_by", sa.Text),
    )
    op.bulk_insert(
        assemblies_table,
        [
            {
                "code": "FRAME-WOOD-PKG-RES",
                "name": "Wood framing package — walls, floor & roof structure",
                "trade": "framing",
                "category": "Framing",
                "unit": "sf_floor",
                "project_type": "residential",
                "description": (
                    "Complete residential wood-frame structure priced per sq ft "
                    "of floor area, the way framing subtrades bid it. Studs, "
                    "plates, headers, blocking, floor + roof framing, sheathing, "
                    "framing hardware."
                ),
                "scope_notes": (
                    "Excludes cladding, insulation, drywall, MEP. Derive future "
                    "rates as framing subcontract lump-sum ÷ takeoff floor area."
                ),
                "created_by": "alembic-0025",
            },
            {
                "code": "DRYWALL-58-HANG-TAPE-L4",
                "name": "Drywall — 5/8\" board hung, taped & finished (Level 4)",
                "trade": "drywall",
                "category": "Drywall & Insulation",
                "unit": "sqft",
                "project_type": "any",
                "description": (
                    "5/8\" Type-X gypsum board installed, taped and finished to a "
                    "Level 4 finish (ready for primer), priced per sq ft of board."
                ),
                "scope_notes": (
                    "Board + mud + tape + screws + sundries + install. Excludes "
                    "insulation, primer/paint. Derive future rates as drywall "
                    "subcontract lump-sum ÷ takeoff board area."
                ),
                "created_by": "alembic-0025",
            },
        ],
    )

    # ── Seed one composed rate entry per pilot assembly ────────────────
    # Installed rate anchored to the publicly-cited Metro-Van market midpoint;
    # productivity back-solved so installed = material + labour_rate × hours.
    # INSERT…SELECT looks up the assembly id by code (no id juggling).
    _entry_sql = text(
        """
        INSERT INTO rate_entries
            (assembly_id, unit_rate, unit, source_type, confidence,
             project_type, source_ref, payload, created_by)
        SELECT id, CAST(:rate AS numeric), :unit, 'composed', 'reference',
               :ptype, :sref, CAST(:payload AS jsonb), 'alembic-0025'
        FROM rate_assemblies WHERE code = :code
        """
    )
    # Framing: market $18–30/sf floor → midpoint $24.00; material ≈ 40% of
    # package ($10/sf); labour $34.97/hr → back-solved 0.400 hr/sf floor.
    op.execute(
        _entry_sql.bindparams(
            rate="24.0000",
            unit="sf_floor",
            ptype="residential",
            sref="Composed: WorkBC framer $34.97/hr + seeded materials, calibrated to Metro-Van market $18–30/sf floor",
            payload=json.dumps({
                "derivation": "composed; installed anchored to market midpoint, productivity back-solved",
                "market_installed_range_cad_sf_floor": [18.0, 30.0],
                "material_cad_sf_floor": 10.0,
                "material_basis": "approx 40% of framing package (lumber+sheathing+hardware); refine with recipe or subcontract-derived",
                "labour_rate_cad_hr": 34.97,
                "labour_basis": "WorkBC NOC 72310 high 43.71 x 0.80",
                "productivity_hr_sf_floor": 0.400,
                "productivity_basis": "back-solved from market midpoint; within cited 0.25-0.40 hr/sf",
            }),
            code="FRAME-WOOD-PKG-RES",
        )
    )
    # Drywall: market $2.50–4.00/sf → midpoint $3.25; material $1.26/sf
    # (5/8 Type-X + finishing, seeded); labour $32/hr → back-solved 0.062 hr/sf.
    op.execute(
        _entry_sql.bindparams(
            rate="3.2500",
            unit="sqft",
            ptype="any",
            sref="Composed: WorkBC drywaller $32.00/hr + seeded materials, calibrated to Metro-Van market $2.50–4.00/sf",
            payload=json.dumps({
                "derivation": "composed; installed anchored to market midpoint, productivity back-solved",
                "market_installed_range_cad_sf": [2.50, 4.00],
                "material_cad_sf": 1.26,
                "material_basis": "5/8\" Type-X $28/32sf +10% waste + finishing $0.30/sf (seeded market materials)",
                "labour_rate_cad_hr": 32.00,
                "labour_basis": "WorkBC NOC 73102 high 40.00 x 0.80",
                "productivity_hr_sf": 0.062,
                "productivity_basis": "back-solved from market midpoint (below textbook 0.11-0.18 because market price reflects apprentice-blended crews + volume material)",
            }),
            code="DRYWALL-58-HANG-TAPE-L4",
        )
    )


def downgrade() -> None:
    op.drop_index("ix_labour_rates_trade", table_name="labour_rates")
    op.drop_table("labour_rates")

    op.drop_index("ix_material_rates_trade", table_name="material_rates")
    op.drop_table("material_rates")

    op.drop_index("ix_rate_entries_active_lookup", table_name="rate_entries")
    op.drop_index("ix_rate_entries_project", table_name="rate_entries")
    op.drop_index("ix_rate_entries_assembly", table_name="rate_entries")
    op.drop_table("rate_entries")

    op.drop_index("ix_rate_assemblies_trade", table_name="rate_assemblies")
    op.drop_table("rate_assemblies")
