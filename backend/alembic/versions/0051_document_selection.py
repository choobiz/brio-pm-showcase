"""Per-project document selection: which files feed the scope, which feed quantities.

Before this, two different gates decided what the AI actually read, and neither
was visible to the person running it:

  · scope analysis took whatever document_ids the modal sent, and silently
    dropped anything that was not a PDF or an image;
  · the CAD takeoff read every .dwg/.dxf whose `category` happened to be
    'drawing' — which on 105-107 Maple meant 4 of the 25 sheets the issuer
    had stamped _DWG_, because the other 21 were filed as permit or survey.

Filing category is a librarian's label. It is not a decision about what to
price from, and using it as one made the estimate quietly narrower than the
evidence available. This table makes the decision explicit and durable: the
office picks, the pick is recorded, and re-running a job uses the same set.

Two independent flags rather than one, because the two purposes want different
files. A DWG carries no narrative and contributes nothing to a scope; a site
photo carries no measurable geometry and contributes nothing to a takeoff. A
permit-set PDF is usefully both.

Revision ID: 0051_document_selection
Revises: 0050_change_order_detail
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0051_document_selection"
down_revision = "0050_change_order_detail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_selections",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("use_for_scope", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("use_for_quantities", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name="fk_document_selections_project_id_projects", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"],
            name="fk_document_selections_document_id_documents", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"],
            name="fk_document_selections_updated_by_id_users", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("project_id", "document_id", name="pk_document_selections"),
    )
    # Every read is "the selection for this project", and the CAD takeoff reads
    # only the quantities side of it.
    op.create_index(
        "ix_document_selections_project", "document_selections", ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_selections_project", table_name="document_selections")
    op.drop_table("document_selections")
