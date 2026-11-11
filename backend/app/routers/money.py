"""Money tab: the summary, preparing the next milestone invoice, and a
change-order reminder. Invoices and change orders keep their own routers."""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_office, require_project_member
from app.db import get_db
from app.services import money as M

# Office only (rollout part 3, 2026-10-15): signed in as the test CREW account,
# these answered with money (contract, owing, estimate costs and margins,
# invoices, rates). The field pages never call them.
router = APIRouter(prefix="/projects/{project_id}/money", tags=["money"],
                   dependencies=[Depends(require_office), Depends(require_project_member)])


@router.get("")
def money_summary(project_id: UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    return M.summary(db, project_id)


@router.post("/prepare-next")
def prepare_next_invoice(project_id: UUID, db: Session = Depends(get_db),
                         user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """A DRAFT progress invoice for the next uninvoiced schedule milestone.

    Goes through the normal create path (numbering, totals, GST, zero-total
    refusal). Idempotent per milestone: pressing Prepare twice returns the same
    draft rather than taking a second invoice number. Nothing is sent.
    """
    from app.routers.invoices import create_invoice
    from app.schemas.invoice import InvoiceCreateRequest

    s = M.summary(db, project_id)
    nxt = s["next_milestone"]
    if nxt is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Every milestone on the payment schedule already has an invoice."
                            if s["schedule_count"] else "This project has no payment schedule.")
    if nxt["amount_before_gst"] is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="The next milestone has no amount or percentage to bill.")
    # Terms follow the last invoice that had any; otherwise the invoice default.
    terms = next((inv for inv in reversed(s["invoices"])), None)
    payload: dict[str, Any] = {
        "date": date.today().isoformat(),
        "description": f"Progress billing: {nxt['milestone']}",
        "milestone": nxt["milestone"],
        "scheduleIndex": nxt["index"],
        "lineItems": [{"description": nxt["milestone"], "amount": nxt["amount_before_gst"],
                       **({"percentage": nxt["pct"]} if nxt["pct"] is not None else {})}],
    }
    if terms:
        from sqlalchemy import text as _T
        prior = db.execute(_T("SELECT payload->>'terms' FROM invoices WHERE id = :i"),
                           {"i": terms["id"]}).scalar()
        if prior:
            payload["terms"] = prior
    inv = create_invoice(
        InvoiceCreateRequest(project_id=project_id, type="progress", payload=payload,
                             idempotency_key=f"next-milestone-{project_id}-{nxt['index']}"),
        Response(), db=db, user=user)
    return {"invoice_id": str(inv.id), "number": inv.number, "milestone": nxt["milestone"]}


@router.post("/change-orders/{co_id}/remind")
def remind_change_order(project_id: UUID, co_id: UUID, db: Session = Depends(get_db),
                        user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """Send the client the change-order notice again. Pending only."""
    from app.models.change_order import ChangeOrder
    from app.services.notify import notify_owner_change_order

    co = db.get(ChangeOrder, co_id)
    if co is None or co.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="change order not found")
    if co.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Only a change order waiting on the client can be reminded.")
    # Best-effort, as on submit: the notifier logs a failure rather than raising.
    notify_owner_change_order(db, co)
    return {"reminded": True}
