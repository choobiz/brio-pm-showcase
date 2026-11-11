"""Add the document_synthesis AI job kind.

"What is missing or contradictory across these documents?" — the question §02
rates a strong win precisely because it has no home in any existing UI. A drawing
viewer shows one sheet, a document list shows filenames, and nothing anywhere
reads a set of documents against each other.

Runs as a JOB rather than a synchronous call, deliberately. A permit set is
twenty PDFs; reading them takes longer than a request should live, and a
synthesis that dies because someone navigated away is worse than one that takes
a minute. The existing poll/stream surface already handles that.

Revision id kept short: alembic_version.version_num is varchar(32), and an
earlier migration in this project failed its bookkeeping UPDATE at 33 characters.

Revision ID: 0059_doc_synthesis
Revises: 0058_quantity_source_bbox
"""
from alembic import op

revision = "0059_doc_synthesis"
down_revision = "0058_quantity_source_bbox"
branch_labels = None
depends_on = None

NEW_KIND = "document_synthesis"


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block, and
    # Alembic wraps a migration in one. The guard makes it re-runnable: adding
    # a value that already exists is an error, not a no-op.
    op.execute("COMMIT")
    exists = op.get_bind().exec_driver_sql(
        "SELECT 1 FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        f"WHERE t.typname = 'ai_job_kind' AND e.enumlabel = '{NEW_KIND}'"
    ).first()
    if not exists:
        op.execute(f"ALTER TYPE ai_job_kind ADD VALUE '{NEW_KIND}'")


def downgrade() -> None:
    # PostgreSQL cannot drop an enum value, and an unused label costs nothing.
    pass
