from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import TimestampedRead


InvoiceType = Literal["progress", "tm", "change_order", "retainer"]
InvoiceStatus = Literal["draft", "sent", "paid", "partial", "overdue", "void"]


class InvoiceBase(BaseModel):
    project_id: UUID
    number: str
    type: InvoiceType
    payload: dict[str, Any] = {}
    status: InvoiceStatus = "draft"
    paid_amount: Decimal = Decimal("0")
    paid_at: datetime | None = None


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceRead(TimestampedRead, InvoiceBase):
    pass


class InvoiceCreateRequest(BaseModel):
    """Operator-facing create — the server assigns the number and computes
    subtotal/GST/total from the type-specific payload."""

    project_id: UUID
    type: InvoiceType
    payload: dict[str, Any] = {}
    #: Generated once when the create form opens and reused on retry, so a
    #: repeated POST returns the invoice already created rather than taking a
    #: second number out of the sequence.
    #:
    #: Carried in the BODY rather than an Idempotency-Key header, which is the
    #: usual convention, because the Next.js proxy at api/backend/[...path]
    #: forwards an explicit allow-list of headers (Content-Type, Accept, Cookie)
    #: and silently drops everything else. A header would arrive as None and the
    #: protection would be absent exactly when it looked present.
    idempotency_key: str | None = None


class InvoicePatch(BaseModel):
    # Optional so the status can be changed on its own. It used to be required,
    # which made a status-only PATCH a 422 and forced the caller to echo the
    # whole payload back just to mark an invoice void.
    payload: dict[str, Any] | None = None
    status: InvoiceStatus | None = None


class InvoicePdfResult(BaseModel):
    document_id: UUID
    filename: str
    size_bytes: int
    download_url: str


class InvoiceSendResult(BaseModel):
    sent_to: str
    document_id: UUID
    status: str


class InvoiceSeed(BaseModel):
    """A proposed invoice payload derived from a project's active estimate.

    Returned by GET /invoices/seed-from-estimate. Nothing is persisted; the
    caller reviews the numbers and posts them to create the invoice.
    """

    payload: dict[str, Any]
    estimate_reference: str | None = None
    estimate_version_number: int | None = None
    estimate_subtotal: float = 0.0
    matches_estimate_subtotal: bool = False


class ChangeOrderInvoiceSeed(BaseModel):
    """A proposed invoice payload derived from an approved change order.

    The estimate seed cannot serve this: a change order bills work that is by
    definition NOT in the estimate, so seeding it from the active estimate
    version would bill the base contract twice. The breakdown on the change
    order is the correct source.
    """

    payload: dict[str, Any]
    change_order_reference: str | None = None
    change_order_status: str | None = None
    change_order_subtotal: float = 0.0
    matches_change_order: bool = False
    #: Non-blocking warnings — e.g. billing a change order the owner has not
    #: approved yet. Surfaced in the form; never a hard refusal, because
    #: BRIO does bill unapproved work occasionally and that is a business call.
    warnings: list[str] = []
