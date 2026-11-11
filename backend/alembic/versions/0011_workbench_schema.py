"""workbench schema — versioned estimates, schedules, cash flows, SOVs, contracts, narratives.

Phase 0 of the brio-pm UI workbench build (per docs/brio-pm-ui-plan.md, approved 2026-06-03).

Adds the following tables:
  - estimate_versions       — 16-trade master estimate versioning (replaces single-version estimates table for new format)
  - schedules               — CPM project schedules
  - activities              — CPM activities (child of schedules)
  - inspections             — inspection milestones (child of schedules)
  - cash_flows              — weekly cash flow scenarios
  - schedules_of_values     — per-trade × per-month draw breakdown
  - contracts               — CCDC 2 contracts with status workflow
  - narratives              — markdown content tied to any parent record
  - audit_logs              — cross-cutting change log
  - drawing_line_item_links — line item ↔ drawing sheet/tag references
  - quick_estimates         — lightweight preliminary scope+estimate (carry over from brio-admin)

Plus:
  - users.role column added for RBAC (admin / editor / viewer)

All tables are ADDITIVE — no breaking changes to existing brio-pm schema.

Revision ID: 0011_workbench_schema
Revises: 0010_quantity_overrides
Create Date: 2026-06-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0011_workbench_schema"
down_revision: Union[str, None] = "0010_quantity_overrides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: users.role column already exists (added in an earlier migration
    # with enum user_role = {owner, admin, viewer}). For the workbench we
    # use the existing roles:
    #   - owner  → top-level (BRIO ownership / system control)
    #   - admin  → team admin (read + write + admin actions)
    #   - viewer → read-only
    # No new role added; downstream API enforces these on workbench routes.

    # ── estimate_versions ────────────────────────────────────────────────
    op.create_table(
        "estimate_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("cost_basis", sa.Text(), nullable=False, server_default="LOW"),
        sa.Column("overhead_pct", sa.Numeric(5, 2), nullable=False, server_default="5"),
        sa.Column("profit_pct", sa.Numeric(5, 2), nullable=False, server_default="8"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("estimate_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("project_id", "version_number", name="uq_estimate_versions_project_version"),
    )
    op.create_index("ix_estimate_versions_project", "estimate_versions", ["project_id"])

    # ── schedules ────────────────────────────────────────────────────────
    op.create_table(
        "schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("project_start", sa.Date(), nullable=False),
        sa.Column("working_hours", sa.Text(), nullable=False,
                  server_default="Mon-Fri 7am-5pm (50 hr/week)"),
        sa.Column("holidays", postgresql.JSONB(), nullable=True),
        sa.Column("deterministic_finish_date", sa.Date(), nullable=True),
        sa.Column("p50_finish_date", sa.Date(), nullable=True),
        sa.Column("p80_finish_date", sa.Date(), nullable=True),
        sa.Column("project_expected_wd", sa.Numeric(6, 2), nullable=True),
        sa.Column("project_std_dev_wd", sa.Numeric(6, 2), nullable=True),
        sa.Column("critical_path_activity_codes", postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("project_id", "version_number", name="uq_schedules_project_version"),
    )
    op.create_index("ix_schedules_project", "schedules", ["project_id"])

    # ── activities (child of schedules) ──────────────────────────────────
    op.create_table(
        "activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("predecessor_codes", postgresql.JSONB(), nullable=True),
        sa.Column("lag_wd", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("optimistic_wd", sa.Integer(), nullable=True),
        sa.Column("most_likely_wd", sa.Integer(), nullable=True),
        sa.Column("pessimistic_wd", sa.Integer(), nullable=True),
        sa.Column("expected_wd", sa.Numeric(6, 2), nullable=True),
        sa.Column("variance", sa.Numeric(10, 4), nullable=True),
        sa.Column("early_start_wd", sa.Integer(), nullable=True),
        sa.Column("early_finish_wd", sa.Integer(), nullable=True),
        sa.Column("late_start_wd", sa.Integer(), nullable=True),
        sa.Column("late_finish_wd", sa.Integer(), nullable=True),
        sa.Column("total_float_wd", sa.Integer(), nullable=True),
        sa.Column("free_float_wd", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("finish_date", sa.Date(), nullable=True),
        sa.Column("is_critical", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_milestone", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("schedule_id", "activity_code", name="uq_activities_schedule_code"),
    )
    op.create_index("ix_activities_schedule", "activities", ["schedule_id"])

    # ── inspections (child of schedules) ─────────────────────────────────
    op.create_table(
        "inspections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("activities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("authority", sa.Text(), nullable=False),
        sa.Column("inspection_type", sa.Text(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        sa.Column("gates", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_inspections_schedule", "inspections", ["schedule_id"])

    # ── cash_flows ───────────────────────────────────────────────────────
    op.create_table(
        "cash_flows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("scenario", sa.Text(), nullable=False),  # 'base' | 'combined'
        sa.Column("based_on_estimate_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("estimate_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("based_on_schedule_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("owner_net_days", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("lien_holdback_pct", sa.Numeric(5, 2), nullable=False, server_default="10"),
        sa.Column("lien_release_days_post_substantial", sa.Integer(), nullable=False, server_default="55"),
        sa.Column("deficiency_days_post_occupancy", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("total_pretax", sa.Numeric(14, 2), nullable=True),
        sa.Column("final_cash_position", sa.Numeric(14, 2), nullable=True),
        sa.Column("min_cash_position", sa.Numeric(14, 2), nullable=True),
        sa.Column("min_position_week_end", sa.Date(), nullable=True),
        sa.Column("bridge_financing_required", sa.Numeric(14, 2), nullable=True),
        sa.Column("weekly_periods", postgresql.JSONB(), nullable=False),
        sa.Column("phase_costs", postgresql.JSONB(), nullable=True),
        sa.Column("milestone_dates", postgresql.JSONB(), nullable=True),
        sa.Column("key_dates", postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cash_flows.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("project_id", "version_number", "scenario",
                            name="uq_cash_flows_project_version_scenario"),
    )
    op.create_index("ix_cash_flows_project", "cash_flows", ["project_id"])

    # ── schedules_of_values ──────────────────────────────────────────────
    op.create_table(
        "schedules_of_values",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("scenario", sa.Text(), nullable=False),  # 'base' | 'combined'
        sa.Column("based_on_estimate_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("estimate_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("based_on_schedule_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("total_pretax", sa.Numeric(14, 2), nullable=True),
        sa.Column("months", postgresql.JSONB(), nullable=False),
        sa.Column("rows", postgresql.JSONB(), nullable=False),
        sa.Column("monthly_totals", postgresql.JSONB(), nullable=True),
        sa.Column("cumulative_totals", postgresql.JSONB(), nullable=True),
        sa.Column("lien_holdback_10pct", postgresql.JSONB(), nullable=True),
        sa.Column("owner_net_monthly", postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("schedules_of_values.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("project_id", "version_number", "scenario",
                            name="uq_sov_project_version_scenario"),
    )
    op.create_index("ix_sov_project", "schedules_of_values", ["project_id"])

    # ── contracts ────────────────────────────────────────────────────────
    op.create_table(
        "contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("template_type", sa.Text(), nullable=False, server_default="CCDC2"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        # status workflow: draft → issued → signed → executed
        sa.Column("owner_name", sa.Text(), nullable=True),
        sa.Column("owner_address", sa.Text(), nullable=True),
        sa.Column("base_contract_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("option_contract_value", sa.Numeric(14, 2), nullable=True),
        sa.Column("based_on_estimate_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("estimate_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("based_on_schedule_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("based_on_sov_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("schedules_of_values.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_start_date", sa.Date(), nullable=True),
        sa.Column("substantial_completion_date", sa.Date(), nullable=True),
        sa.Column("final_completion_date", sa.Date(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_by_owner", sa.Text(), nullable=True),
        sa.Column("signed_by_contractor", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("project_id", "version_number", name="uq_contracts_project_version"),
    )
    op.create_index("ix_contracts_project", "contracts", ["project_id"])
    op.create_index("ix_contracts_status", "contracts", ["status"])

    # ── narratives ───────────────────────────────────────────────────────
    op.create_table(
        "narratives",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_table", sa.Text(), nullable=False),
        # parent_table values: 'schedules', 'cash_flows', 'estimate_versions',
        # 'contracts', 'projects' (free-form), 'drawing_audit'
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("narratives.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_narratives_parent", "narratives", ["parent_table", "parent_id"])
    op.create_index("ix_narratives_project", "narratives", ["project_id"])

    # ── audit_logs ───────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("row_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),  # create | update | delete | migrate
        sa.Column("diff", postgresql.JSONB(), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_logs_project_created", "audit_logs", ["project_id", "created_at"])
    op.create_index("ix_audit_logs_row", "audit_logs", ["table_name", "row_id"])

    # ── drawing_line_item_links ──────────────────────────────────────────
    op.create_table(
        "drawing_line_item_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("estimate_version_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("estimate_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_name", sa.Text(), nullable=False),
        sa.Column("line_item_index", sa.Integer(), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sheet_number", sa.Text(), nullable=True),
        sa.Column("tag_reference", sa.Text(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_drawing_links_estimate", "drawing_line_item_links", ["estimate_version_id"])
    op.create_index("ix_drawing_links_document", "drawing_line_item_links", ["document_id"])

    # ── quick_estimates ──────────────────────────────────────────────────
    # Lightweight scope+estimate for preliminary pricing (no invoices).
    # Carry over from internal-ops/brio-admin/data/quick-estimates/.
    op.create_table(
        "quick_estimates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("client_name", sa.Text(), nullable=True),
        sa.Column("client_email", sa.Text(), nullable=True),
        sa.Column("client_phone", sa.Text(), nullable=True),
        sa.Column("project_type", sa.Text(), nullable=True),
        sa.Column("sqft", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        # status: active | converted | abandoned
        sa.Column("converted_project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_quick_estimates_status", "quick_estimates", ["status"])


def downgrade() -> None:
    op.drop_index("ix_quick_estimates_status", table_name="quick_estimates")
    op.drop_table("quick_estimates")

    op.drop_index("ix_drawing_links_document", table_name="drawing_line_item_links")
    op.drop_index("ix_drawing_links_estimate", table_name="drawing_line_item_links")
    op.drop_table("drawing_line_item_links")

    op.drop_index("ix_audit_logs_row", table_name="audit_logs")
    op.drop_index("ix_audit_logs_project_created", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_narratives_project", table_name="narratives")
    op.drop_index("ix_narratives_parent", table_name="narratives")
    op.drop_table("narratives")

    op.drop_index("ix_contracts_status", table_name="contracts")
    op.drop_index("ix_contracts_project", table_name="contracts")
    op.drop_table("contracts")

    op.drop_index("ix_sov_project", table_name="schedules_of_values")
    op.drop_table("schedules_of_values")

    op.drop_index("ix_cash_flows_project", table_name="cash_flows")
    op.drop_table("cash_flows")

    op.drop_index("ix_inspections_schedule", table_name="inspections")
    op.drop_table("inspections")

    op.drop_index("ix_activities_schedule", table_name="activities")
    op.drop_table("activities")

    op.drop_index("ix_schedules_project", table_name="schedules")
    op.drop_table("schedules")

    op.drop_index("ix_estimate_versions_project", table_name="estimate_versions")
    op.drop_table("estimate_versions")

    # NOTE: users.role column not dropped here — it existed before this
    # migration and is used by NextAuth/auth elsewhere.
