"""document_chunks + pgvector — RAG semantic-retrieval store

Revision ID: 0043_document_chunks_pgvector
Revises: 0042_project_inbound_token
Create Date: 2026-07-31

Backs app/services/rag.py's PgVectorStore. Raw SQL (op.execute) so the migration
needs no pgvector Python type at author time; the extension is created first,
then the vector column can be typed. Embedding dim 1024 = Voyage `voyage-3`.
"""

from __future__ import annotations

from alembic import op


revision = "0043_document_chunks_pgvector"
down_revision = "0042_project_inbound_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE document_chunks (
            id          BIGSERIAL PRIMARY KEY,
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
            ordinal     INTEGER NOT NULL,
            text        TEXT NOT NULL,
            embedding   vector(1024) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_document_chunks_document_id ON document_chunks (document_id)")
    op.execute("CREATE INDEX ix_document_chunks_project_id ON document_chunks (project_id)")
    # ivfflat cosine index for approximate nearest-neighbour retrieval.
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding "
        "ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_chunks")
    # Leave the `vector` extension installed — other objects may depend on it.
