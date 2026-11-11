"""POST /invoices consumes an invoice number, so a retry must not create a second one.

On 2026-10-06 a create was made from the invoices page. The API logged one
POST /invoices returning 201 and no 4xx or 5xx anywhere in a two-hour window,
while the browser showed an error: the invoice was created and the response was
lost returning through the Vercel proxy, which calls fetch() with no timeout and
sets no maxDuration. A lost response is indistinguishable from a failed create,
so the obvious user response - press the button again - would have taken a
second number out of the sequence for a duplicate.

The number is the reason this matters more here than on an ordinary create.
`_next_invoice_number` draws from a sequence, and an invoice number that was
issued and then abandoned leaves a hole in a billing record with no explanation
in it.

The key is supplied by the client, generated once when the create form opens and
REUSED on retry. Nullable, because every invoice created before this migration
has none and because the key is only meaningful for a create that came through
the form.

UNIQUE rather than unique-per-project: the client generates a UUID, so a
collision across projects would be a client bug, and finding out loudly beats
quietly attaching one project's retry to another's invoice. Postgres permits
many NULLs in a unique index, so existing rows are unaffected.

Revision ID: 0065_invoice_idempotency
Revises: 0064_qa_checklists
"""
from alembic import op
import sqlalchemy as sa

revision = "0065_invoice_idempotency"
down_revision = "0064_qa_checklists"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.create_index(
        "uq_invoices_idempotency_key",
        "invoices",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_invoices_idempotency_key", table_name="invoices")
    op.drop_column("invoices", "idempotency_key")
