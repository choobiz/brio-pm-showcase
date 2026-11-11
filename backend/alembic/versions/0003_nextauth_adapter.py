"""NextAuth v5 Postgres adapter tables.

The frontend installs @auth/pg-adapter which expects four tables — `users`,
`accounts`, `sessions`, `verification_token` — addressed unqualified (no
schema prefix). We extend the existing `users` table with the two columns
the adapter writes (`emailVerified`, `image`) and create the other three
fresh. With `session.strategy = "jwt"` the `sessions` table is never
populated, but we still create it because the adapter's TypeScript
interface includes the methods.

Revision ID: 0003_nextauth_adapter
Revises: 0002_tracks_a_f_g
Create Date: 2026-05-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003_nextauth_adapter"
down_revision: Union[str, None] = "0002_tracks_a_f_g"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extend the existing users table. NextAuth writes these on createUser /
    # updateUser; the backend ignores them.
    op.add_column("users", sa.Column("emailVerified", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("image", sa.Text(), nullable=True))

    op.create_table(
        "accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "userId",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("providerAccountId", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text()),
        sa.Column("access_token", sa.Text()),
        sa.Column("expires_at", sa.BigInteger()),
        sa.Column("id_token", sa.Text()),
        sa.Column("scope", sa.Text()),
        sa.Column("session_state", sa.Text()),
        sa.Column("token_type", sa.Text()),
        sa.UniqueConstraint("provider", "providerAccountId", name="uq_accounts_provider_providerAccountId"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "userId",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sessionToken", sa.Text(), nullable=False, unique=True),
    )

    op.create_table(
        "verification_token",
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("expires", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("identifier", "token", name="pk_verification_token"),
    )


def downgrade() -> None:
    op.drop_table("verification_token")
    op.drop_table("sessions")
    op.drop_table("accounts")
    op.drop_column("users", "image")
    op.drop_column("users", "emailVerified")
