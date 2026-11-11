from uuid import UUID

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


class Scope(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "scopes"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    # The scope_analysis AIJob that produced this scope. NULL for scopes
    # imported from brio-admin or created by hand. SET NULL on job delete.
    ai_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ai_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # The estimate version this scope was derived from (Mode 2 — scope-from-
    # estimate). NULL for the bootstrap scope (Mode 1 — scope-from-documents).
    based_on_estimate_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("estimate_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
