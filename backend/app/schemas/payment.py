from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import TimestampedRead


class PaymentCreate(BaseModel):
    amount: Decimal
    method: str  # E-Transfer | Cheque | Cash | …
    paid_at: datetime | None = None
    notes: str | None = None


class PaymentRead(TimestampedRead):
    invoice_id: UUID
    amount: Decimal
    method: str
    paid_at: datetime
    notes: str | None = None
