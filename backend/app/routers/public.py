"""Public router — no authentication.

Serves the client-facing invoice view. The signed token in the URL is the
only credential; everything here returns a deliberately public-safe subset of
invoice data (no audit log, no internal references).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.invoice import Invoice
from app.models.project import Project
from app.services.r2 import object_exists, presign_get
from app.services.share_token import verify_invoice_token

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/invoice/{token}")
def public_invoice(token: str, db: Session = Depends(get_db)) -> dict:
    """Client-facing invoice view, addressed by signed token (no auth)."""
    invoice_id = verify_invoice_token(token)
    if invoice_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invalid link")
    try:
        invoice = db.get(Invoice, uuid.UUID(invoice_id))
    except (ValueError, TypeError):
        invoice = None
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")

    project = db.get(Project, invoice.project_id)
    p = invoice.payload or {}

    # Offer the branded PDF if one has already been generated for this invoice.
    pdf_key = f"invoices/{invoice.id}/{invoice.number}.pdf"
    pdf_url = presign_get(pdf_key) if object_exists(pdf_key) else None

    total = float(p.get("total") or 0)
    paid = float(invoice.paid_amount or 0)
    return {
        "number": invoice.number,
        "type": invoice.type,
        "status": invoice.status,
        "project_name": getattr(project, "name", None),
        "date": p.get("date"),
        "terms": p.get("terms"),
        "description": p.get("description"),
        "milestone": p.get("milestone"),
        "line_items": p.get("lineItems") or [],
        "labor": p.get("labor") or [],
        "materials": p.get("materials") or [],
        "subtotal": p.get("subtotal"),
        "gst": p.get("gst"),
        "total": total,
        "paid": paid,
        "balance_due": round(total - paid, 2),
        "notes": p.get("notes"),
        "pdf_url": pdf_url,
    }


@router.get("/presentation/{token}")
def public_presentation_pdf(token: str, db: Session = Depends(get_db)):
    """Client-facing presentation proposal PDF, addressed by signed token (no auth)."""
    import json as _json

    from fastapi.responses import Response
    from sqlalchemy import text

    from app.services.presentation_pdf import render_presentation_pdf
    from app.services.share_token import verify_presentation_token

    pid = verify_presentation_token(token)
    if pid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invalid link")
    row = db.execute(
        text("SELECT payload, project_id FROM presentations WHERE id = :i"), {"i": pid},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    payload = row["payload"]
    if isinstance(payload, str):
        payload = _json.loads(payload)
    project = db.execute(text("SELECT * FROM projects WHERE id = :p"), {"p": row["project_id"]}).mappings().first()
    project = dict(project) if project else {}
    client = None
    if project.get("client_id"):
        c = db.execute(text("SELECT * FROM clients WHERE id = :c"), {"c": project["client_id"]}).mappings().first()
        client = dict(c) if c else None
    pdf = render_presentation_pdf(payload, project, client)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="proposal.pdf"'})
