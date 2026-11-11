"""Postgres sequence for invoice numbering.

Track E numbers invoices `INV-{YYYY}-{NNNN}`. The numeric counter comes from a
Postgres sequence so `nextval` is atomic — concurrent invoice creates can
never collide, and the existing UNIQUE constraint on `invoices.number` is the
backstop.

Revision ID: 0007_invoice_number_seq
Revises: 0006_estimate_version_job
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0007_invoice_number_seq"
down_revision: Union[str, None] = "0006_estimate_version_job"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS invoice_number_seq START 1")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS invoice_number_seq")
