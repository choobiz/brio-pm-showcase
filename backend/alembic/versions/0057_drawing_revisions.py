"""Record which drawings are the same sheet, and which revision is in force

Documents had no revision concept at all: "Rev C" lived in a filename, and two
revisions of one sheet were unrelated rows. Measured against ISO 19650, that is
the foundation everything else rests on — a quantity traced to a superseded
drawing is traced to nothing.

WHAT IS STORED IS THE HUMAN'S DECISION, NOT THE MACHINE'S GUESS.

`services/drawing_register` proposes groupings from filenames. A filename is
evidence about a drawing and not a fact about one, so nothing it infers is
written until a person accepts it. These columns hold what was accepted.

Deliberately NO `is_current` or `superseded_by` column. Current is DERIVED:
within a group, the member with the latest issue date and highest revision that
is not a duplicate. Storing it would be a second copy of a fact that can drift
from the first, which is the defect this codebase has spent real effort removing
elsewhere — and it would need rewriting across the whole group every time one
file is added.

Revision ID: 0057_drawing_revisions
Revises: 0056_division_sets
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0057_drawing_revisions"
down_revision = "0056_division_sets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Which drawing this file is a version OF. Revisions of one sheet share it.
    # Not a foreign key to anything: the group has no independent existence, it
    # is just the identity the reviewer asserted.
    op.add_column("documents",
                  sa.Column("drawing_group_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_documents_drawing_group", "documents", ["drawing_group_id"])

    # As confirmed, which is not necessarily as parsed. "R3", "Rev B", "PRELIM".
    op.add_column("documents", sa.Column("revision_label", sa.Text(), nullable=True))

    # The date the revision was issued, where one is known. Primary sort for
    # "which is current": a drawing issued in November supersedes one issued in
    # October whatever the labels say.
    op.add_column("documents", sa.Column("issued_on", sa.Date(), nullable=True))

    # This file IS another file — a re-upload, a "Copy of", or the same drawing
    # exported to another CAD format. Distinct from being an earlier revision,
    # and the distinction matters: a duplicate never contributes a revision.
    op.add_column("documents",
                  sa.Column("duplicate_of_id", postgresql.UUID(as_uuid=True),
                            sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True))

    # Who accepted the grouping and when. Without this the register cannot tell
    # "reviewed and found to be one version" from "never looked at" — the same
    # distinction the quantity gate draws, and for the same reason.
    op.add_column("documents",
                  sa.Column("register_reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents",
                  sa.Column("register_reviewed_by_id", postgresql.UUID(as_uuid=True),
                            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "register_reviewed_by_id")
    op.drop_column("documents", "register_reviewed_at")
    op.drop_column("documents", "duplicate_of_id")
    op.drop_column("documents", "issued_on")
    op.drop_column("documents", "revision_label")
    op.drop_index("ix_documents_drawing_group", table_name="documents")
    op.drop_column("documents", "drawing_group_id")
