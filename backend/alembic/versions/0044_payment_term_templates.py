"""payment_term_templates — reusable payment/draw schedule templates (seeded)

Revision ID: 0044_payment_term_templates
Revises: 0043_document_chunks_pgvector
Create Date: 2026-07-31
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0044_payment_term_templates"
down_revision = "0043_document_chunks_pgvector"
branch_labels = None
depends_on = None

_HOLDBACK = ("Substantial completion & occupancy — holdback", 10,
             "Final finishes, commissioning, cleaning, deficiency correction and final inspections. "
             "Released once the work is complete and accepted (aligns with the BC Builders Lien Act 10% holdback).")

_BUILTINS = [
    ("BRIO Standard (30/30/30/10)", "Deposit on signing, two progress draws, final holdback.", [
        {"milestone": "Deposit — on signing", "pct": 30, "detail": "Secures the contract, permits, sub-trade deposits and site setup, and funds the first phase of work — site prep, demolition, excavation and foundation."},
        {"milestone": "Foundation, framing & weathertight (lock-up)", "pct": 30, "detail": "Structural framing, roof and membrane, exterior windows/doors and cladding to a weathertight lock-up."},
        {"milestone": "MEP rough-in, insulation, drywall & interior finishes", "pct": 30, "detail": "Plumbing/electrical/HVAC rough-in, insulation, drywall, paint, flooring, millwork and fixtures."},
        {"milestone": _HOLDBACK[0], "pct": _HOLDBACK[1], "detail": _HOLDBACK[2]},
    ]),
    ("Permit-split (10/20/30/30/10)", "Small deposit at signing, larger draw once the permit is issued.", [
        {"milestone": "Deposit — on signing", "pct": 10, "detail": "Secures the contract and lets us book the survey, lock in the excavation crew and order initial materials so we are ready to start the day the permit lands."},
        {"milestone": "Building permit issued — mobilization", "pct": 20, "detail": "Due when the building permit is issued; funds permit fees, sub-trade deposits, site setup and the first phase of work — site prep, demolition, excavation and foundation."},
        {"milestone": "Foundation, framing & weathertight (lock-up)", "pct": 30, "detail": "Structural framing, roof and membrane, exterior windows/doors and cladding to a weathertight lock-up."},
        {"milestone": "MEP rough-in, insulation, drywall & interior finishes", "pct": 30, "detail": "Plumbing/electrical/HVAC rough-in, insulation, drywall, paint, flooring, millwork and fixtures."},
        {"milestone": _HOLDBACK[0], "pct": _HOLDBACK[1], "detail": _HOLDBACK[2]},
    ]),
    ("BC Custom Home (7-stage)", "Milestone draws for a full new build.", [
        {"milestone": "Deposit — on signing", "pct": 10, "detail": "Secures the contract, permits and mobilization."},
        {"milestone": "Foundation complete", "pct": 20, "detail": "Excavation, forming, footings and foundation walls."},
        {"milestone": "Framing / structure complete", "pct": 20, "detail": "Wall, floor and roof framing and the structural package."},
        {"milestone": "Lock-up (weathertight)", "pct": 15, "detail": "Roofing, exterior windows and doors, and cladding — building dried-in."},
        {"milestone": "MEP rough-ins complete", "pct": 15, "detail": "Plumbing, electrical and HVAC rough-in with inspections."},
        {"milestone": "Drywall & interior finishes", "pct": 10, "detail": "Insulation, drywall, paint, flooring, millwork and fixtures."},
        {"milestone": "Substantial completion — holdback", "pct": 10, "detail": _HOLDBACK[2]},
    ]),
    ("Major Renovation (front-loaded)", "Compressed draws for a gut renovation.", [
        {"milestone": "Deposit — on signing", "pct": 10, "detail": "Secures the contract, permits and mobilization."},
        {"milestone": "Demolition & site start", "pct": 20, "detail": "Demolition, protection and site preparation."},
        {"milestone": "Framing & building envelope", "pct": 20, "detail": "Structural changes, framing and envelope work."},
        {"milestone": "MEP rough-ins", "pct": 20, "detail": "Plumbing, electrical and HVAC rough-in with inspections."},
        {"milestone": "Interior finishes", "pct": 20, "detail": "Drywall, paint, flooring, tile, millwork and fixtures."},
        {"milestone": "Substantial completion — holdback", "pct": 10, "detail": _HOLDBACK[2]},
    ]),
    ("Small job (thirds)", "Simple three-payment schedule for small jobs.", [
        {"milestone": "Deposit — on signing", "pct": 34, "detail": "Secures the contract and materials."},
        {"milestone": "At rough-in", "pct": 33, "detail": "On completion of demolition and rough-in work."},
        {"milestone": "Completion", "pct": 33, "detail": "On substantial completion and final acceptance."},
    ]),
]


def upgrade() -> None:
    op.create_table(
        "payment_term_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("entries", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    conn = op.get_bind()
    for name, desc, entries in _BUILTINS:
        conn.execute(
            sa.text("INSERT INTO payment_term_templates (name, description, is_builtin, entries) "
                    "VALUES (:n, :d, true, CAST(:e AS jsonb))"),
            {"n": name, "d": desc, "e": json.dumps(entries)},
        )


def downgrade() -> None:
    op.drop_table("payment_term_templates")
