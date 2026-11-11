"""Milestone bookings and letters (plan approved by Amir 2026-10-16).

For each stage of a job: who has to be booked, how far ahead, which documents
the inspector will ask for, and the emails that book them. Built for a project
from the library (services/milestone_library.py), confirmed by the PM, tied to
schedule activities by code.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin

#: Who a milestone item is with. The project team maps each role to a person.
TEAM_ROLES = ("municipality", "structural_engineer", "geotechnical_engineer", "surveyor",
              "stormwater_engineer", "envelope_professional", "energy_advisor", "arborist",
              "electrician", "plumber", "concrete_supplier", "truss_supplier", "contractor", "other")
#: An item's progress, in order. not_needed is the PM saying it doesn't apply.
MILESTONE_STATUSES = ("to_book", "requested", "booked", "received", "done", "not_needed")


class ProjectTeamMember(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "project_team"

    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
                                             nullable=False, index=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class ProjectMilestone(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "project_milestones"
    __table_args__ = (UniqueConstraint("project_id", "ref", name="uq_project_milestones_ref"),)

    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
                                             nullable=False, index=True)
    #: Short reference carried in email subjects ("[KS-M7]") so replies file back here.
    ref: Mapped[str] = mapped_column(Text, nullable=False)
    template_key: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    party_role: Mapped[str] = mapped_column(Text, nullable=False)
    #: booking | document_request | submittal | notice
    email_kind: Mapped[str] = mapped_column(Text, nullable=False, default="booking", server_default="booking")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="to_book", server_default="to_book")
    lead_days: Mapped[int | None] = mapped_column(Integer)
    lead_note: Mapped[str | None] = mapped_column(Text)
    #: The schedule activity it hangs off, by code (schedules are versioned).
    activity_code: Mapped[str | None] = mapped_column(Text)
    #: [{name, status: missing|received|not_needed, document_id?, note?}]
    required_docs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    why: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    booked_for: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    #: The PM has looked at the generated list and kept this item.
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    #: Emails out and replies in, newest last: [{at, direction, communication_id?,
    #: email_id?, from?, subject, document_ids: []}]. Replies file here by the
    #: [KS-Mn] tag in the subject (the project address is BCC'd and in Reply-To).
    thread: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="system", server_default="system")


class MilestoneEmail(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "milestone_emails"

    milestone_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True),
                                               ForeignKey("project_milestones.id", ondelete="CASCADE"),
                                               nullable=False, index=True)
    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
                                             nullable=False, index=True)
    to_email: Mapped[str | None] = mapped_column(Text)
    to_name: Mapped[str | None] = mapped_column(Text)
    cc: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    attachment_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    #: drafting | draft | sent | failed
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft", server_default="draft")
    error: Mapped[str | None] = mapped_column(Text)
    ai_job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_by: Mapped[str | None] = mapped_column(Text)
    #: The exact message as sent (from, to, reply-to, bcc), for the record.
    sent_envelope: Mapped[dict | None] = mapped_column(JSONB)
