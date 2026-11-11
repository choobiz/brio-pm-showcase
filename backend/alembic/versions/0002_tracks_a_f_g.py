"""tracks A + F + G — auth (no schema), AI cost columns, client CRM extensions

Track A: User.role enum was already shipped in 0001_initial; no schema change.
Track F: AI job cost / retry tracking columns.
Track G: Client CRM normalized arrays, tags, dates, notes, GIN indexes.

Revision ID: 0002_tracks_a_f_g
Revises: 0001_initial
Create Date: 2026-05-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_tracks_a_f_g"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Track F: ai_jobs cost + retry columns ──────────────────────────────
    op.add_column("ai_jobs", sa.Column("model", sa.Text(), nullable=True))
    op.add_column(
        "ai_jobs",
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ai_jobs",
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ai_jobs",
        sa.Column(
            "cost_usd",
            sa.Numeric(precision=10, scale=4),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "ai_jobs",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )

    # ── Track G: clients CRM extensions ────────────────────────────────────
    op.add_column(
        "clients",
        sa.Column("normalized_name", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_clients_normalized_name", "clients", ["normalized_name"])

    for col in (
        "emails",
        "normalized_emails",
        "phones",
        "normalized_phones",
        "addresses",
        "normalized_addresses",
        "tags",
    ):
        op.add_column(
            "clients",
            sa.Column(
                col,
                postgresql.ARRAY(sa.Text()),
                nullable=False,
                server_default="{}",
            ),
        )

    op.add_column("clients", sa.Column("first_seen", sa.Date(), nullable=True))
    op.add_column("clients", sa.Column("last_seen", sa.Date(), nullable=True))
    op.add_column("clients", sa.Column("notes", sa.Text(), nullable=True))

    op.create_index(
        "ix_clients_normalized_emails_gin",
        "clients",
        ["normalized_emails"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_clients_normalized_phones_gin",
        "clients",
        ["normalized_phones"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_clients_normalized_addresses_gin",
        "clients",
        ["normalized_addresses"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_clients_tags_gin",
        "clients",
        ["tags"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    # ── Track G reverse ────────────────────────────────────────────────────
    op.drop_index("ix_clients_tags_gin", table_name="clients")
    op.drop_index("ix_clients_normalized_addresses_gin", table_name="clients")
    op.drop_index("ix_clients_normalized_phones_gin", table_name="clients")
    op.drop_index("ix_clients_normalized_emails_gin", table_name="clients")
    op.drop_column("clients", "notes")
    op.drop_column("clients", "last_seen")
    op.drop_column("clients", "first_seen")
    for col in (
        "tags",
        "normalized_addresses",
        "addresses",
        "normalized_phones",
        "phones",
        "normalized_emails",
        "emails",
    ):
        op.drop_column("clients", col)
    op.drop_index("ix_clients_normalized_name", table_name="clients")
    op.drop_column("clients", "normalized_name")

    # ── Track F reverse ────────────────────────────────────────────────────
    op.drop_column("ai_jobs", "attempts")
    op.drop_column("ai_jobs", "cost_usd")
    op.drop_column("ai_jobs", "output_tokens")
    op.drop_column("ai_jobs", "input_tokens")
    op.drop_column("ai_jobs", "model")
