"""Roles overhaul + DB-driven allowlist

- users.role: user_role enum -> TEXT (new role set: superadmin/manager/worker/owner).
  Migrate the single existing internal 'owner' (Amir) -> 'superadmin'.
  NOTE: 'owner' now means the CLIENT/homeowner (external portal), not BRIO ownership.
- users.active: DB-driven allowlist — a user must be active to sign in
  (retires the AUTH_ALLOWED_EMAILS env allowlist).

Revision ID: 0034_roles_and_active
Revises: 0033_review_requests
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0034_roles_and_active"
down_revision = "0033_review_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # role enum -> text
    op.execute("ALTER TABLE users ALTER COLUMN role DROP DEFAULT")
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE text USING role::text")
    op.execute("UPDATE users SET role = 'superadmin' WHERE role = 'owner'")
    op.execute("UPDATE users SET role = 'manager' WHERE role = 'admin'")
    op.execute("UPDATE users SET role = 'worker' WHERE role = 'viewer'")
    op.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'worker'")

    # DB-driven allowlist
    op.add_column("users", sa.Column("active", sa.Boolean(), nullable=False, server_default="true"))

    # Safety: the superadmin must exist + be active so we can never lock out.
    op.execute("UPDATE users SET role = 'superadmin', active = true WHERE lower(email) = 'amir@sister-co.example'")


def downgrade() -> None:
    op.drop_column("users", "active")
    # Map new roles back to the legacy enum values before casting.
    op.execute("UPDATE users SET role = 'owner' WHERE role = 'superadmin'")
    op.execute("UPDATE users SET role = 'admin' WHERE role = 'manager'")
    op.execute("UPDATE users SET role = 'viewer' WHERE role IN ('worker', 'owner')")
    op.execute("ALTER TABLE users ALTER COLUMN role DROP DEFAULT")
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE user_role USING role::user_role")
    op.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'viewer'")
