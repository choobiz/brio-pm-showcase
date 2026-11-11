"""CRM: opportunities and interactions over the existing contact layer.

`Client` is the contact. These two tables are the *work* done against a contact,
which is a deliberate split — the same firm can be a construction prospect and a
short-term-rental prospect at once, with different offers and different owners,
and duplicating the contact to express that is how a firm ends up in the system
twice.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, TimestampMixin, UUIDPKMixin

# Which prospecting programme this opportunity belongs to. Construction targets
# design firms and GCs; str targets developers of new purpose-built rental.
CRM_TRACKS = ("construction", "str")

CRM_STATUSES = ("new", "working", "engaged", "quoting", "won", "lost", "dormant")
CRM_OPEN_STATUSES = ("new", "working", "engaged", "quoting")

CRM_CHANNELS = ("call", "email", "meeting", "event", "note")
CRM_DIRECTIONS = ("outbound", "inbound")


class CrmOpportunity(UUIDPKMixin, TimestampMixin, Base):
    """One pursuit of one contact on one track."""

    __tablename__ = "crm_opportunities"

    client_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    track: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="new",
                                        server_default="new")

    # The two fields the whole product turns on. An opportunity without a next
    # action is not a pipeline entry, it is a note — and the queue surfaces it
    # as needing one rather than letting it sit invisible.
    next_action: Mapped[str | None] = mapped_column(Text)
    next_action_at: Mapped[date | None] = mapped_column(Date)

    owner_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )
    notes: Mapped[str | None] = mapped_column(Text)
    surfaced_at: Mapped[date | None] = mapped_column(Date)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    client = relationship("Client", lazy="joined")
    interactions = relationship(
        "CrmInteraction", back_populates="opportunity",
        order_by="CrmInteraction.occurred_at.desc()", lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("client_id", "track", name="uq_crm_opp_client_track"),
        Index("ix_crm_opp_due", "next_action_at", "status"),
        Index("ix_crm_opp_owner", "owner_id", "status"),
        Index("ix_crm_opp_track", "track", "status"),
    )

    @property
    def is_open(self) -> bool:
        return self.status in CRM_OPEN_STATUSES


class CrmInteraction(UUIDPKMixin, TimestampMixin, Base):
    """One logged touch — a call, an email, a meeting, or a note."""

    __tablename__ = "crm_interactions"

    client_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    opportunity_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crm_opportunities.id", ondelete="SET NULL"),
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False, default="outbound",
                                           server_default="outbound")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"),
    )
    summary: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
    )

    opportunity = relationship("CrmOpportunity", back_populates="interactions")

    __table_args__ = (
        Index("ix_crm_int_client", "client_id", "occurred_at"),
        Index("ix_crm_int_opp", "opportunity_id"),
    )


# People who sent their details from a BRIO digital business card
# (builtbybrio.com/card/<slug>). Deliberately NOT a Client row: a card lead is
# an unverified stranger at a networking event, and turning every submission
# into a contact is how the contact list fills with junk. Amir triages here and
# promotes the real ones by hand.
CARD_LEAD_ROLES = ("developer", "owner", "builder", "trade", "other")
CARD_LEAD_STATUSES = ("new", "contacted", "archived")


class CrmCardLead(UUIDPKMixin, TimestampMixin, Base):
    """One "send me your details" submission from a business-card page."""

    __tablename__ = "crm_card_leads"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    card_slug: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="new",
                                        server_default="new")
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_crm_card_leads_created_at", "created_at"),
    )
