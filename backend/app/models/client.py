from datetime import date

from sqlalchemy import BigInteger, Date, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


class Client(UUIDPKMixin, TimestampMixin, Base):
    """Client / CRM record.

    Schema is shaped to match the JSON registry in
    ``internal-ops/brio-admin/lib/registry.mjs``. Each contact carries one or
    more emails / phones / addresses (the same client may register a renovation
    under several phone numbers over the years), plus normalized arrays that
    feed dedup / find-or-create matching with the priority
    ``email > phone > name``.

    The legacy singular ``email`` / ``phone`` / ``address`` columns are kept as
    a backwards-compat mirror of the first element of each array; the service
    layer keeps them in sync on every write.
    """

    __tablename__ = "clients"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="", index=True)

    # Legacy singular mirrors of the array columns. Kept so existing W1 callers
    # that still read ``client.email`` / ``client.phone`` / ``client.address``
    # do not break while Tracks B–E migrate.
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)

    emails: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )
    normalized_emails: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )
    phones: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )
    normalized_phones: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )
    addresses: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )
    normalized_addresses: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )

    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}",
    )

    first_seen: Mapped[date | None] = mapped_column(Date)
    last_seen: Mapped[date | None] = mapped_column(Date)

    # ---- CRM / prospecting -------------------------------------------------
    # A prospect and a client live in the same table on purpose: it is what stops
    # the same firm existing twice, and a contact can move from prospect to client
    # without changing identity. Everything below is populated by the permit
    # prospecting pipeline and is null for contacts that arrived any other way.
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="client",
                                      server_default="client")
    segment: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[str | None] = mapped_column(Text)

    permits_total: Mapped[int | None] = mapped_column(Integer)
    permits_as_applicant: Mapped[int | None] = mapped_column(Integer)
    value_as_applicant: Mapped[int | None] = mapped_column(BigInteger)
    cities: Mapped[str | None] = mapped_column(Text)
    top_use_category: Mapped[str | None] = mapped_column(Text)
    permit_name: Mapped[str | None] = mapped_column(Text)
    #: Up to three of this firm's actual recent permits, newest first —
    #: [{address, value, work, issued, permit}]. Denormalised snapshot from the
    #: prospecting pipeline, rewritten wholesale on refresh. Naming a real
    #: address is what separates outreach from a mail merge.
    recent_projects: Mapped[list | None] = mapped_column(JSONB)

    # BC registry (OrgBook). entity_type BC = incorporated, SP/FM = sole proprietor.
    bc_registration: Mapped[str | None] = mapped_column(Text)
    legal_name: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(Text)
    registry_status: Mapped[str | None] = mapped_column(Text)

    contact_confidence: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    # Set for firms that must never be queued — dissolved companies, opt-outs.
    excluded_reason: Mapped[str | None] = mapped_column(Text)

    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # GIN indexes for substring / ANY() search on the normalized arrays.
        Index("ix_clients_normalized_emails_gin", "normalized_emails", postgresql_using="gin"),
        Index("ix_clients_normalized_phones_gin", "normalized_phones", postgresql_using="gin"),
        Index("ix_clients_normalized_addresses_gin", "normalized_addresses", postgresql_using="gin"),
        Index("ix_clients_tags_gin", "tags", postgresql_using="gin"),
        Index("ix_clients_kind_tier", "kind", "tier"),
        Index("ix_clients_segment", "segment"),
    )
