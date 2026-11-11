"""Text pulled from each document, page by page, for searching inside files

"Ask the documents" could only match a file's name or its AI summary, and only
11 of 5,336 documents have a summary. The words inside the files were never
stored anywhere searchable.

Two tables:

* `document_texts` — one row per document that has been, or is waiting to be,
  read. Its status is what the Documents page shows: waiting, searchable,
  scanned with no text layer, or failed with the reason. A row exists only for
  file types that can carry text (PDF, Word, Excel, plain text), so photos and
  video never sit in a queue they can never leave.
* `document_text_pages` — the text of each page, with a generated `tsvector`
  and a GIN index. Postgres full-text search, so no new service and no
  per-file cost; a hit cites the file AND the page.

No backfill here. Existing documents are queued by
`scripts/backfill_document_text.py`, which is dry-run by default: queueing
~1,750 files is a production mass write and goes through its own approval.

Revision ID: 0068_document_text_pages
Revises: 0067_document_category_confirmed
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID

revision = "0068_document_text_pages"
down_revision = "0067_document_category_confirmed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_texts",
        sa.Column("document_id", UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("page_count", sa.Integer),
        sa.Column("pages_with_text", sa.Integer),
        sa.Column("char_count", sa.Integer),
        sa.Column("truncated", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("error", sa.Text),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("extracted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending','running','ok','no_text','too_large','failed')",
            name="ck_document_texts_status"),
    )
    op.create_index("ix_document_texts_project_id", "document_texts", ["project_id"])
    op.create_index("ix_document_texts_status", "document_texts", ["status"])

    op.create_table(
        "document_text_pages",
        sa.Column("document_id", UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("page_no", sa.Integer, primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("tsv", TSVECTOR,
                  sa.Computed("to_tsvector('english', text)", persisted=True)),
    )
    op.create_index("ix_document_text_pages_project_id", "document_text_pages", ["project_id"])
    op.create_index("ix_document_text_pages_tsv", "document_text_pages", ["tsv"],
                    postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_document_text_pages_tsv", table_name="document_text_pages")
    op.drop_index("ix_document_text_pages_project_id", table_name="document_text_pages")
    op.drop_table("document_text_pages")
    op.drop_index("ix_document_texts_status", table_name="document_texts")
    op.drop_index("ix_document_texts_project_id", table_name="document_texts")
    op.drop_table("document_texts")
