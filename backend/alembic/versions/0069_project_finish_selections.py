"""Finish selections, asked and confirmed, instead of a finish tier

`finish_level` (builder-grade / mid / high) applied a pile of defaults nobody
chose. Amir's rule (2026-10-13): finishes are a question-and-answer — the app
proposes an answer for each finish, a person confirms or changes it, and only
confirmed answers reach the estimate. A shell has no finishes at all.

* `finish_selections` — {key: {answer, suggested, why, confirmed, confirmed_at,
  confirmed_by}} for the questions in services/finish_spec.QUESTIONS.
* `part_shell_note` — for delivered_as = part_shell: which areas are left
  unfinished, in words.

`finish_level` is left in place (no data is lost) and no longer asked.

Revision ID: 0069_project_finish_selections
Revises: 0068_document_text_pages
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0069_project_finish_selections"
down_revision = "0068_document_text_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("finish_selections", JSONB, nullable=False,
                                        server_default=sa.text("'{}'::jsonb")))
    op.add_column("projects", sa.Column("part_shell_note", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "part_shell_note")
    op.drop_column("projects", "finish_selections")
