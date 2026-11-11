"""Finish level, delivered-as, and the drawing decisions get a place on the project

The interview has asked "what finish level?" and "finished or shell?" since it
was built, and the answers had nowhere to go: no column held them, so they were
asked on every visit and recorded nowhere. Both swing the number (finish level
runs through flooring, millwork and fixtures together; a shell leaves whole
divisions unpriced), so they belong on the project, next to type and year built,
where the estimate can read them.

`drawing_decisions` records what the estimator decided about a discipline that
has no drawings: price it as an allowance, or it is not in scope. Keyed by
discipline, with "all" for the no-drawings-at-all case. Without it the
"Are there plumbing drawings?" question came back every time it was answered.

All three are nullable / empty: no existing project has an answer, and a
default would be a guess written into the record.

Revision ID: 0066_project_finish_delivery
Revises: 0065_invoice_idempotency
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0066_project_finish_delivery"
down_revision = "0065_invoice_idempotency"
branch_labels = None
depends_on = None

FINISH_LEVELS = ("builder_grade", "mid", "high")
DELIVERED_AS = ("finished", "shell", "part_shell")


def upgrade() -> None:
    op.add_column("projects", sa.Column("finish_level", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("delivered_as", sa.Text(), nullable=True))
    op.add_column(
        "projects",
        sa.Column("drawing_decisions", JSONB(), nullable=False, server_default="{}"),
    )
    op.create_check_constraint(
        "ck_projects_finish_level", "projects",
        f"finish_level IS NULL OR finish_level IN {FINISH_LEVELS!r}",
    )
    op.create_check_constraint(
        "ck_projects_delivered_as", "projects",
        f"delivered_as IS NULL OR delivered_as IN {DELIVERED_AS!r}",
    )


def downgrade() -> None:
    op.drop_constraint("ck_projects_delivered_as", "projects", type_="check")
    op.drop_constraint("ck_projects_finish_level", "projects", type_="check")
    op.drop_column("projects", "drawing_decisions")
    op.drop_column("projects", "delivered_as")
    op.drop_column("projects", "finish_level")
