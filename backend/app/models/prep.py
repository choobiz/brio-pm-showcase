"""Prepared documents (Ask, phase 9 part 2): takeoffs, supplier orders, rental
requests and field sheets drafted from a project's documents, checked by a
person, then rendered as BRIO-branded PDFs.

Approved by Amir 2026-10-13. Drafting runs on his Mac (Claude Code on his
subscription) when the Mac is on, otherwise on the API under a monthly cap.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin

#: Request status. `waiting_user` = questions to answer, or a draft to check.
PREP_STATUSES = ("queued", "running", "waiting_user", "finalized", "failed", "cancelled")
#: What the engine is asked to do next.
PREP_STAGES = ("plan", "draft", "revise")
PREP_KINDS = ("takeoff", "order", "price_request", "rental_request", "field_sheet", "other")


class Supplier(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "suppliers"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    trade: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class CompanyPractice(UUIDPKMixin, TimestampMixin, Base):
    """How BRIO does things that no drawing says: "footing stakes are bought
    ready-made", "the form rental company supplies ties and keys". Read by the
    drafting engine; most first-draft corrections on 1234 were these."""

    __tablename__ = "company_practices"

    topic: Mapped[str] = mapped_column(Text, nullable=False)
    rule: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class PrepRequest(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "prep_requests"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    request: Mapped[str] = mapped_column(Text, nullable=False)
    document_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued", server_default="queued", index=True)
    stage: Mapped[str] = mapped_column(Text, nullable=False, default="plan", server_default="plan")
    #: "mac" or "api", whichever ran the latest stage.
    engine: Mapped[str | None] = mapped_column(Text)
    #: The API engine may take a queued request from this time on; until then
    #: it is the Mac's to claim.
    api_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan: Mapped[dict | None] = mapped_column(JSONB)
    answers: Mapped[dict | None] = mapped_column(JSONB)
    #: The correction to apply when stage == "revise".
    correction: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    error: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=0, server_default="0")
    #: The most the server may spend on this request, cumulative, as approved
    #: by the person step by step (Amir 2026-10-13: approve the cost first).
    approved_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=0, server_default="0")


class PreparedDocument(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "prepared_documents"

    prep_request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("prep_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"))
    #: {header: [{label, value}], sections: [{heading, columns, rows|text}], notes, conflicts}
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft", server_default="draft")
    document_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"))


class DrawingFact(UUIDPKMixin, TimestampMixin, Base):
    """What one drawing page says, read once (tiled) and reused by every later
    request on the project — the read is the expensive part."""

    __tablename__ = "drawing_facts"
    __table_args__ = (UniqueConstraint("document_id", "page_no", name="uq_drawing_facts_page"),)

    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    facts_md: Mapped[str] = mapped_column(Text, nullable=False)
    engine: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)


class AISpend(UUIDPKMixin, TimestampMixin, Base):
    """Every paid API call made for Ask or prepared documents."""

    __tablename__ = "ai_spend"

    purpose: Mapped[str] = mapped_column(Text, nullable=False)  # "prep" | "ask"
    stage: Mapped[str | None] = mapped_column(Text)
    prep_request_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("prep_requests.id", ondelete="SET NULL"))
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    info: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class ProjectGeometry(UUIDPKMixin, TimestampMixin, Base):
    """A project's foundation geometry, proposed by the AI from the drawings and
    CONFIRMED by a person. The forming calculator counts only from a confirmed
    one, so the same job always gives the same quantities."""

    __tablename__ = "project_geometries"

    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="foundation", server_default="foundation")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="proposed", server_default="proposed")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    source: Mapped[str | None] = mapped_column(Text)
    prep_request_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("prep_requests.id", ondelete="SET NULL"))
    confirmed_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
