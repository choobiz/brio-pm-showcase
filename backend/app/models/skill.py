"""Skill registry + telemetry + correction-signal models.

S1 of the auto-improving skill engine. Cross-project-portable: the
`owner_namespace` column on SkillVersion lets the same tables serve
multiple projects when we federate (or extract into a service).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


class SkillVersion(UUIDPKMixin, TimestampMixin, Base):
    """One row per skill version. The active row per
    (skill_id, owner_namespace) drives behaviour at run time; retired
    rows are retained for rollback and history."""

    __tablename__ = "skill_versions"

    skill_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_namespace: Mapped[str] = mapped_column(
        String(64), nullable=False, default="brio-pm"
    )
    # draft | active | retired
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    examples: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    parent_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("skill_versions.id", ondelete="SET NULL"),
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    description: Mapped[str | None] = mapped_column(Text)
    # Approval gate (Step 3): a draft becomes active only via an explicit
    # approve action, which stamps these. Active rows carry the sign-off.
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_note: Mapped[str | None] = mapped_column(Text)


class SkillRun(UUIDPKMixin, TimestampMixin, Base):
    """One row per AI worker invocation. Captures the input + raw output
    + (eventually) the accepted_output once the user has saved or
    discarded. Diffs between raw_output and accepted_output become
    correction_signals."""

    __tablename__ = "skill_runs"

    skill_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("skill_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ai_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ai_jobs.id", ondelete="SET NULL")
    )
    project_type: Mapped[str | None] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    raw_output: Mapped[dict | None] = mapped_column(JSONB)
    accepted_output: Mapped[dict | None] = mapped_column(JSONB)
    # pending | accepted | edited | rejected | discarded
    acceptance_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    edit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CorrectionSignal(UUIDPKMixin, Base):
    """One row per discrete change between raw_output and accepted_output
    for a given skill run. `semantic_category` is set by the save-time
    tag popup; `feeds_learning` is auto-derived from the category and
    drives whether this signal is consumed by candidate generation."""

    __tablename__ = "correction_signals"

    skill_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("skill_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    # added | removed | modified
    diff_type: Mapped[str] = mapped_column(String(16), nullable=False)
    before_value: Mapped[dict | None] = mapped_column(JSONB)
    after_value: Mapped[dict | None] = mapped_column(JSONB)
    semantic_category: Mapped[str | None] = mapped_column(String(64))
    user_note: Mapped[str | None] = mapped_column(Text)
    feeds_learning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
