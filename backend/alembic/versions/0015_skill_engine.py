"""skill_versions + skill_runs + correction_signals — S1 plumbing.

Skill registry + run telemetry + correction signals. v1.0 of the three
in-repo skills (scope_analyzer, estimate_generator, document_summary)
gets seeded as the active row. Cross-project-portable via
`owner_namespace` — when the second project plugs in, it federates
in the same tables (or extracts into a service).

Revision ID: 0015_skill_engine
Revises: 0014_document_summaries
Create Date: 2026-06-18
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision = "0015_skill_engine"
down_revision = "0014_document_summaries"
branch_labels = None
depends_on = None


# v1.0 prompts are intentionally a marker, not a fully-substituted Jinja
# template — the live prompt code in app/services/ai.py and
# app/services/document_summary_prompts.py still runs the show in S1. The
# registry's job at S1 is to provide a stable version anchor for the
# telemetry + correction signals. S3 starts writing candidate templates
# that actually replace the embedded code.
_SKILL_SEED = [
    {
        "skill_id": "scope_analyzer",
        "version": "1.0",
        "description": (
            "Per-trade scope generator. Reads project notes + photos and "
            "produces a structured scope payload (summary, categories, "
            "materials, permits, etc.). Implementation lives in "
            "app/services/ai.py::build_scope_analysis_messages."
        ),
        "prompt_template_ref": "app/services/ai.py::_SCOPE_SYSTEM_PROMPT",
    },
    {
        "skill_id": "estimate_generator",
        "version": "1.0",
        "description": (
            "Fully-itemized estimate generator. Consumes scope + project "
            "context + benchmarks + verified quantities, emits a category-"
            "grouped estimate JSON. Implementation lives in "
            "app/services/ai.py::build_estimate_messages."
        ),
        "prompt_template_ref": "app/services/ai.py::_ESTIMATE_SYSTEM_PROMPT",
    },
    {
        "skill_id": "document_summary",
        "version": "1.0",
        "description": (
            "Per-file document indexer for the project-level MD index. "
            "Category-aware prompts produce a summary + structured fields. "
            "Implementation lives in "
            "app/services/document_summary_prompts.py."
        ),
        "prompt_template_ref": (
            "app/services/document_summary_prompts.py::"
            "build_messages_for_document_summary"
        ),
    },
]


def upgrade() -> None:
    # ── skill_versions ─────────────────────────────────────────────────
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("skill_id", sa.String(64), nullable=False, index=True),
        sa.Column("version", sa.String(32), nullable=False),
        # Owner / tenant namespace. NULL is not allowed; default is the
        # main brio-pm tenant. When the engine federates across projects
        # this column becomes the cross-tenant key.
        sa.Column("owner_namespace", sa.String(64), nullable=False, server_default="brio-pm"),
        # draft | active | retired
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("prompt_template", sa.Text, nullable=False),
        sa.Column("output_schema", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("examples", sa.dialects.postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "parent_version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skill_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="system"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("skill_id", "version", "owner_namespace", name="uq_skill_versions_id_version_ns"),
    )
    # One active row per skill per namespace — partial unique index.
    op.create_index(
        "uq_skill_versions_active",
        "skill_versions",
        ["skill_id", "owner_namespace"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # ── skill_runs — one row per AI worker invocation ──────────────────
    op.create_table(
        "skill_runs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "skill_version_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skill_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ai_job_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Project type at run-time — drives commercial-vs-residential eval split.
        sa.Column("project_type", sa.String(64), nullable=True),
        sa.Column("input", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("raw_output", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("accepted_output", sa.dialects.postgresql.JSONB, nullable=True),
        # pending | accepted | edited | rejected | discarded
        sa.Column("acceptance_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("edit_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_skill_runs_version", "skill_runs", ["skill_version_id"])
    op.create_index("ix_skill_runs_status", "skill_runs", ["acceptance_status"])
    op.create_index("ix_skill_runs_job", "skill_runs", ["ai_job_id"])

    # ── correction_signals — diffs from user edits ─────────────────────
    op.create_table(
        "correction_signals",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "skill_run_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skill_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # JSON-pointer-style path: "categories/5/items/12"
        sa.Column("path", sa.Text, nullable=False),
        # added | removed | modified
        sa.Column("diff_type", sa.String(16), nullable=False),
        sa.Column("before_value", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("after_value", sa.dialects.postgresql.JSONB, nullable=True),
        # Semantic taxonomy from the save-time tag popup. NULL = not tagged.
        # Only the "ai_miss_*" categories drive candidate generation.
        # Documented set:
        #   ai_miss_missed_item, ai_miss_wrong_qty_unit_pricing,
        #   ai_miss_wrong_category, ai_miss_doesnt_apply,
        #   project_owner_preference, project_site_condition,
        #   project_code_permit_nuance, other
        sa.Column("semantic_category", sa.String(64), nullable=True),
        sa.Column("user_note", sa.Text, nullable=True),
        # Whether this signal feeds the candidate-generation pipeline.
        # Auto-set true for ai_miss_* tags, false otherwise.
        sa.Column("feeds_learning", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_correction_signals_run", "correction_signals", ["skill_run_id"])
    op.create_index("ix_correction_signals_category", "correction_signals", ["semantic_category"])
    op.create_index(
        "ix_correction_signals_feeds_learning",
        "correction_signals",
        ["feeds_learning"],
        postgresql_where=sa.text("feeds_learning = true"),
    )

    # ── Seed v1.0 of the three in-repo skills ──────────────────────────
    # Use raw SQL via op.bulk_insert so the migration doesn't depend on
    # the ORM model definitions (which can drift independently).
    skill_versions_table = sa.table(
        "skill_versions",
        sa.column("skill_id", sa.String),
        sa.column("version", sa.String),
        sa.column("owner_namespace", sa.String),
        sa.column("status", sa.String),
        sa.column("prompt_template", sa.Text),
        sa.column("description", sa.Text),
        sa.column("created_by", sa.String),
    )
    rows = []
    for s in _SKILL_SEED:
        rows.append(
            {
                "skill_id": s["skill_id"],
                "version": s["version"],
                "owner_namespace": "brio-pm",
                "status": "active",
                # We store a *reference* to where the live prompt lives in
                # source code. The candidate-generation pipeline (S3) will
                # start writing actual templates here.
                "prompt_template": (
                    f"# v1.0 reference\n# Live prompt lives in: "
                    f"{s['prompt_template_ref']}\n"
                    "# Bumped to a real template body by the candidate-"
                    "generation pipeline (S3) when the first auto-"
                    "improvement candidate is approved."
                ),
                "description": s["description"],
                "created_by": "alembic-0015",
            }
        )
    op.bulk_insert(skill_versions_table, rows)


def downgrade() -> None:
    op.drop_index("ix_correction_signals_feeds_learning", table_name="correction_signals")
    op.drop_index("ix_correction_signals_category", table_name="correction_signals")
    op.drop_index("ix_correction_signals_run", table_name="correction_signals")
    op.drop_table("correction_signals")

    op.drop_index("ix_skill_runs_job", table_name="skill_runs")
    op.drop_index("ix_skill_runs_status", table_name="skill_runs")
    op.drop_index("ix_skill_runs_version", table_name="skill_runs")
    op.drop_table("skill_runs")

    op.drop_index("uq_skill_versions_active", table_name="skill_versions")
    op.drop_table("skill_versions")
