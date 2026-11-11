"""Supplier price-sheet imports: staged, applied, undoable (plan phase 4)

Amir approved 2026-10-16 ("Phase 4: Library Pages and Import").

Revision ID: 0075_catalogue_imports
Revises: 0074_starter_library
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0075_catalogue_imports"
down_revision = "0074_starter_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalogue_imports",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("supplier", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="staged"),
        sa.Column("column_map", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("rows", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("summary", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("changes", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("applied_by", sa.Text()),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("undone_by", sa.Text()),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_catalogue_imports_supplier_status", "catalogue_imports", ["supplier", "status"])


def downgrade() -> None:
    op.drop_index("ix_catalogue_imports_supplier_status", table_name="catalogue_imports")
    op.drop_table("catalogue_imports")
