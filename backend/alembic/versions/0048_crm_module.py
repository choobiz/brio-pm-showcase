"""crm module — prospects, opportunities and interactions

Revision ID: 0048_crm_module
Revises: 0047_analysis_packs
Create Date: 2026-09-01

Turns the permit-derived target list into something workable. The contact layer
already exists — `clients` carries multi-email/phone/address with normalised
arrays, GIN indexes and a working find-or-create/merge, which is the hard part
of any CRM. What was missing is everything that makes a contact list a *queue*.

Three changes:

1. `clients` gains prospect columns. Deliberately NOT a second contact table:
   one table is what stops the same firm existing twice, and a developer can be
   a construction prospect and a short-term-rental prospect simultaneously.

2. `crm_opportunities` carries the TRACK. Track belongs on the work, not on the
   contact — one firm, two pipelines, different offers. `next_action_at` is the
   field the entire product turns on; without it a CRM becomes a filing cabinet
   nobody opens.

3. `crm_interactions` is separate from `project_communications` rather than
   making that table's `project_id` nullable. That column is load-bearing for
   the projects module and loosening a constraint the rest of the app relies on
   to serve a new feature is how unrelated things break later.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0048_crm_module"
down_revision = "0047_analysis_packs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- 1. prospect columns on the existing contact table -----------------
    for col in (
        sa.Column("kind", sa.Text(), nullable=False,
                  server_default="client"),          # client | prospect
        sa.Column("segment", sa.Text()),             # design | gc | design_build | …
        sa.Column("tier", sa.Text()),                # A | B | C | D
        sa.Column("permits_total", sa.Integer()),
        sa.Column("permits_as_applicant", sa.Integer()),
        sa.Column("value_as_applicant", sa.BigInteger()),
        sa.Column("cities", sa.Text()),
        sa.Column("top_use_category", sa.Text()),
        sa.Column("permit_name", sa.Text()),         # original free-text permit string
        sa.Column("bc_registration", sa.Text()),
        sa.Column("legal_name", sa.Text()),
        sa.Column("entity_type", sa.Text()),         # BC = incorporated, SP/FM = sole prop
        sa.Column("registry_status", sa.Text()),
        sa.Column("contact_confidence", sa.Text()),  # high | medium | low
        sa.Column("website", sa.Text()),
        sa.Column("excluded_reason", sa.Text()),     # e.g. dissolved — keep, never queue
    ):
        op.add_column("clients", col)

    op.create_index("ix_clients_kind_tier", "clients", ["kind", "tier"])
    op.create_index("ix_clients_segment", "clients", ["segment"])

    # ---- 2. opportunities — where TRACK lives ------------------------------
    op.create_table(
        "crm_opportunities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("client_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("track", sa.Text(), nullable=False),      # construction | str
        sa.Column("status", sa.Text(), nullable=False, server_default="new"),
        sa.Column("next_action", sa.Text()),
        sa.Column("next_action_at", sa.Date()),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("notes", sa.Text()),
        sa.Column("surfaced_at", sa.Date()),                # phase 2: date-triggered
        sa.Column("last_contacted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        # one open opportunity per contact per track — two would split the history
        sa.UniqueConstraint("client_id", "track", name="uq_crm_opp_client_track"),
    )
    # the queue query: due or overdue, by owner. This index IS the product.
    op.create_index("ix_crm_opp_due", "crm_opportunities",
                    ["next_action_at", "status"])
    op.create_index("ix_crm_opp_owner", "crm_opportunities", ["owner_id", "status"])
    op.create_index("ix_crm_opp_track", "crm_opportunities", ["track", "status"])

    # ---- 3. interactions ---------------------------------------------------
    op.create_table(
        "crm_interactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("client_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("crm_opportunities.id", ondelete="SET NULL")),
        sa.Column("channel", sa.Text(), nullable=False),    # call | email | meeting | event | note
        sa.Column("direction", sa.Text(), nullable=False, server_default="outbound"),
        sa.Column("occurred_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("summary", sa.Text()),
        sa.Column("outcome", sa.Text()),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_crm_int_client", "crm_interactions",
                    ["client_id", "occurred_at"])
    op.create_index("ix_crm_int_opp", "crm_interactions", ["opportunity_id"])


def downgrade() -> None:
    op.drop_table("crm_interactions")
    op.drop_table("crm_opportunities")
    op.drop_index("ix_clients_segment", table_name="clients")
    op.drop_index("ix_clients_kind_tier", table_name="clients")
    for c in ("excluded_reason", "website", "contact_confidence", "registry_status",
              "entity_type", "legal_name", "bc_registration", "permit_name",
              "top_use_category", "cities", "value_as_applicant",
              "permits_as_applicant", "permits_total", "tier", "segment", "kind"):
        op.drop_column("clients", c)
