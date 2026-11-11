"""Review solicitation — a Google-review ask sent to a happy customer or trade.

Amir/Sam enter a recipient + a note about the job; the AI drafts a humble,
personal review-request message (talking points, never a copy-paste review),
which they send from their own device via a mailto:/sms: deep-link. We store
the request so we have a log of who's been asked.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, UUIDPKMixin


PERSON_TYPES = ("customer", "trade")
REVIEW_CHANNELS = ("email", "sms")
REVIEW_STATUSES = ("draft", "sent")


class ReviewRequest(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "review_requests"

    # Who created the ask (nullable so the log survives a user deletion).
    requested_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True,
    )
    requester_name: Mapped[str | None] = mapped_column(Text)

    # Recipient
    recipient_name: Mapped[str] = mapped_column(Text, nullable=False)
    person_type: Mapped[str] = mapped_column(Text, nullable=False, default="customer", server_default="customer")
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)

    # The drafted ask + delivery
    message: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str | None] = mapped_column(Text)  # email | sms
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft", server_default="draft")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
