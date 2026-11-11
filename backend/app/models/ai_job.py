from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


AI_JOB_KINDS = (
    "scope_analysis",
    "estimate_generation",
    "email_draft",
    "ocr",
    "document_summary",  # P1 indexing
    "cad_takeoff",       # deterministic CAD (DWG/DXF) quantity takeoff — scoped
    "estimate_full",     # full multi-discipline estimate (scope-based or CAD-based)
    "presentation",      # client-facing presentation deck (assemble + AI framing)
    "scope_from_estimate",     # derive a scope-of-work FROM an estimate version
    "schedule_from_estimate",  # AI-assisted CPM schedule FROM an estimate version
    "change_order_generation", # price extra work from documents -> draft change order
    "ai_edit",           # edit a scope/estimate/change order from a written instruction
    "document_synthesis",  # read a document SET against itself: gaps, disagreements
    "daily_report_capture",  # spoken site day -> PROPOSED daily report payload
    "submittal_review",  # shop drawing / quote checked against governing documents
    "drawing_analysis",  # PyMuPDF parse of a drawing PDF for the measure page (no AI)
)
AI_JOB_STATUSES = ("queued", "running", "done", "error")


class AIJob(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "ai_jobs"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    kind: Mapped[str] = mapped_column(Enum(*AI_JOB_KINDS, name="ai_job_kind"), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*AI_JOB_STATUSES, name="ai_job_status"),
        nullable=False,
        default="queued",
        server_default="queued",
        index=True,
    )
    input: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    output: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Track F: cost + retry tracking
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0"), server_default="0",
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    #: What the job is doing, appended to as it goes. See migration 0063 for why
    #: a list and not a count: "3 of 6" cannot say WHICH three, and during a
    #: seven-minute wait the useful question is what it is doing now.
    steps: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
