from sqlalchemy import Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


# Role set (as of 2026-07-22). Internal roles are hierarchical (a higher rank
# includes the capabilities of lower ranks); `owner` is the EXTERNAL client /
# homeowner (portal-only), NOT BRIO ownership.
USER_ROLES = ("superadmin", "manager", "worker", "owner")

# Internal capability rank. `owner` (client) sits at 0 — no internal access.
#   worker    → field app (assigned projects)
#   manager   → office + field (assigned projects)
#   superadmin→ everything, global, user management
ROLE_RANK = {"owner": 0, "worker": 1, "manager": 2, "superadmin": 3}


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(
        Text, nullable=False, default="worker", server_default="worker",
    )
    # DB-driven allowlist: a user must be active to sign in. Superadmin toggles
    # this from /admin/users (retires the AUTH_ALLOWED_EMAILS env allowlist).
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true",
    )
