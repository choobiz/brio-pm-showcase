"""An invoice is dated paid on the day it was paid, not the day it was entered.

2026-10-15: INV-2026-10059 (1234) was paid Friday 9 Oct and entered on the 15th;
_recompute_payment_state stamped paid_at with now(), so the invoice would have
read as paid on the 15th.
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text as _T

from app.models.invoice import Invoice
from app.models.payment import Payment
from app.routers.invoices import _recompute_payment_state


def test_paid_at_is_the_last_payments_date(db_session):
    pid = uuid4()
    db_session.execute(_T("INSERT INTO projects (id, name, slug) VALUES (:i, 'Paid', :s)"),
                       {"i": str(pid), "s": f"pd-{pid.hex[:8]}"})
    inv = Invoice(project_id=pid, number=f"INV-PD-{pid.hex[:6]}", type="progress",
                  payload={"total": 100.0}, status="sent")
    db_session.add(inv)
    db_session.flush()
    first, last = datetime(2026, 10, 2, tzinfo=timezone.utc), datetime(2026, 10, 9, 19, tzinfo=timezone.utc)
    db_session.add_all([Payment(invoice_id=inv.id, amount=Decimal("40"), method="Cheque", paid_at=first),
                        Payment(invoice_id=inv.id, amount=Decimal("60"), method="Cheque", paid_at=last)])
    db_session.flush()
    _recompute_payment_state(db_session, inv)
    assert inv.status == "paid" and inv.paid_amount == Decimal("100")
    assert inv.paid_at == last
