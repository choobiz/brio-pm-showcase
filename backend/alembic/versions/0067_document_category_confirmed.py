"""Record when a person confirmed a document's category

The Documents page needs a "Needs a category" folder, and a category alone
cannot say whether anyone looked: "other" is both what the filename guesser
falls back to and a legitimate answer for a file that is genuinely other.
These two columns hold the confirmation, the same way `register_reviewed_at`
does for drawing revisions.

Nullable with no backfill. No existing row records who chose its category
(imports, recovery and the guesser all wrote the column), so any value written
here now would be a claim nobody made.

Revision ID: 0067_document_category_confirmed
Revises: 0066_project_finish_delivery
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0067_document_category_confirmed"
down_revision = "0066_project_finish_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("category_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column(
            "category_confirmed_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL",
                          name="fk_documents_category_confirmed_by_id_users"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_constraint("fk_documents_category_confirmed_by_id_users", "documents",
                       type_="foreignkey")
    op.drop_column("documents", "category_confirmed_by_id")
    op.drop_column("documents", "category_confirmed_at")
