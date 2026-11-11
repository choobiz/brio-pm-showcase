from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel, TimestampedRead


class ClientFindOrCreate(BaseModel):
    """Payload for ``POST /clients/find-or-create``.

    Mirrors the shape of ``client`` blocks in the brio-admin project files —
    the importer feeds rows from those files straight into this schema.
    """

    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None


class ClientCreate(ClientFindOrCreate):
    notes: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class ClientUpdate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None


class ClientRead(TimestampedRead):
    """Full client row — used by detail responses and the find-or-create
    return envelope."""

    name: str
    normalized_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    emails: list[str] = Field(default_factory=list)
    normalized_emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    normalized_phones: list[str] = Field(default_factory=list)
    addresses: list[str] = Field(default_factory=list)
    normalized_addresses: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    first_seen: Optional[date] = None
    last_seen: Optional[date] = None
    notes: Optional[str] = None


class ClientListItem(ORMModel):
    """Trimmed row for ``GET /clients`` — what the list table renders."""

    id: UUID
    name: str
    primary_email: Optional[str] = None
    primary_phone: Optional[str] = None
    project_count: int = 0
    last_activity: Optional[date] = None
    tags: list[str] = Field(default_factory=list)


class ClientFindOrCreateResponse(BaseModel):
    client: ClientRead
    created: bool


class ClientProjectSummary(ORMModel):
    id: UUID
    slug: str
    name: str
    address: Optional[str] = None
    status: str
    contract_value: Decimal = Decimal("0")
    total_billed: Decimal = Decimal("0")
    total_paid: Decimal = Decimal("0")


class ClientDocumentSummary(ORMModel):
    id: UUID
    project_id: UUID
    category: str
    filename: str
    created_at: date


class ClientInvoiceSummary(ORMModel):
    id: UUID
    project_id: UUID
    number: str
    type: str
    status: str
    total: Decimal = Decimal("0")
    paid_amount: Decimal = Decimal("0")


class ClientPaymentSummary(ORMModel):
    id: UUID
    invoice_id: UUID
    method: str
    amount: Decimal
    paid_at: date


class ClientReadDetail(ClientRead):
    """Full client detail — used by ``GET /clients/{id}``."""

    projects: list[ClientProjectSummary] = Field(default_factory=list)
    documents: list[ClientDocumentSummary] = Field(default_factory=list)
    invoices: list[ClientInvoiceSummary] = Field(default_factory=list)
    payments: list[ClientPaymentSummary] = Field(default_factory=list)
    total_billed: Decimal = Decimal("0")
    total_paid: Decimal = Decimal("0")
    total_outstanding: Decimal = Decimal("0")
    last_activity: Optional[date] = None


class TagPayload(BaseModel):
    tag: str
