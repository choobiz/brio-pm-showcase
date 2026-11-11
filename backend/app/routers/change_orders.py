"""Change orders — office side (manager/superadmin).

Draft → submit (pending) → owner approves/rejects in the portal. Approved COs
adjust the client's revised contract total + schedule.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office, real_user_id
from app.db import get_db
from app.models.change_order import ChangeOrder
from app.services.revisions import actor_uuid, snapshot


router = APIRouter(prefix="/projects/{project_id}/change-orders", tags=["change-orders"])


class COCreate(BaseModel):
    title: str
    description: str | None = None
    cost_impact: float = 0
    schedule_impact_days: int = 0


class COUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    cost_impact: float | None = None
    schedule_impact_days: int | None = None
    # The pricing breakdown. When it carries line items the total is derived
    # from them and any cost_impact sent alongside is ignored, so the figure on
    # the change order and the figure the client is walked through cannot
    # disagree. Correcting a quantity is the normal edit here: the generated
    # numbers are inferred from a photo and a description.
    detail: dict[str, Any] | None = None


def co_read(co: ChangeOrder) -> dict[str, Any]:
    cost = float(co.cost_impact)
    return {
        "id": str(co.id), "number": co.number, "reference": f"CO-{co.number}",
        "title": co.title, "description": co.description,
        "cost_impact": cost, "cost_impact_incl_gst": round(cost * 1.05, 2),
        "schedule_impact_days": co.schedule_impact_days, "status": co.status,
        "decided_at": co.decided_at.isoformat() if co.decided_at else None,
        "owner_note": co.owner_note,
        "created_at": co.created_at.isoformat() if co.created_at else None,
        "detail": co.detail or {},
    }


def _get(db: Session, project_id: UUID, co_id: UUID) -> ChangeOrder:
    co = db.get(ChangeOrder, co_id)
    if co is None or co.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="change order not found")
    return co


@router.get("")
def list_cos(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> list[dict]:
    assert_project_access(db, user, project_id)
    rows = db.execute(
        select(ChangeOrder).where(ChangeOrder.project_id == project_id).order_by(ChangeOrder.number)
    ).scalars().all()
    return [co_read(c) for c in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_co(
    project_id: UUID,
    body: COCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    assert_project_access(db, user, project_id)
    nxt = (db.execute(
        select(ChangeOrder.number).where(ChangeOrder.project_id == project_id)
        .order_by(ChangeOrder.number.desc()).limit(1)
    ).scalar() or 0) + 1
    co = ChangeOrder(
        project_id=project_id, number=nxt, title=body.title, description=body.description,
        cost_impact=Decimal(str(body.cost_impact)), schedule_impact_days=body.schedule_impact_days,
        status="draft", created_by_id=real_user_id(user),
    )
    db.add(co)
    db.commit()
    db.refresh(co)
    return co_read(co)


@router.patch("/{co_id}")
def update_co(
    project_id: UUID,
    co_id: UUID,
    body: COUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    assert_project_access(db, user, project_id)
    co = _get(db, project_id, co_id)
    if co.status in ("approved", "rejected", "void"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="a decided change order can't be edited")
    # Snapshot before anything is overwritten. The line items, assumptions and
    # exclusions are the argument behind the price; losing the previous version
    # of them to a mis-click loses the reasoning, not just the number.
    snapshot(
        db, table="change_orders", row_id=co.id, project_id=project_id,
        action="edit", actor_id=actor_uuid(user),
    )
    if body.title is not None:
        co.title = body.title
    if body.description is not None:
        co.description = body.description
    if body.schedule_impact_days is not None:
        co.schedule_impact_days = body.schedule_impact_days

    derived: Decimal | None = None
    if body.detail is not None:
        detail = dict(body.detail)
        items = [i for i in (detail.get("line_items") or []) if isinstance(i, dict)]
        cleaned = []
        for it in items:
            desc = str(it.get("description") or "").strip()
            if not desc:
                continue  # a row with no description is a row someone abandoned
            try:
                amount = Decimal(str(it.get("amount") or 0))
            except (InvalidOperation, TypeError, ValueError):
                amount = Decimal("0")
            cleaned.append({**it, "description": desc, "amount": float(amount)})
        if cleaned:
            detail["line_items"] = cleaned
            derived = sum((Decimal(str(i["amount"])) for i in cleaned), Decimal("0"))
        else:
            detail["line_items"] = []
        co.detail = detail

    if derived is not None:
        # Line items win. Letting a typed total override its own breakdown is
        # how a client gets walked through figures that do not add up.
        co.cost_impact = derived
    elif body.cost_impact is not None:
        co.cost_impact = Decimal(str(body.cost_impact))

    db.commit()
    db.refresh(co)
    return co_read(co)


@router.post("/{co_id}/submit")
def submit_co(
    project_id: UUID,
    co_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Send the CO to the owner for approval (draft → pending)."""
    assert_project_access(db, user, project_id)
    co = _get(db, project_id, co_id)
    if co.status != "draft":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="only a draft can be submitted")
    co.status = "pending"
    from datetime import datetime as _dt, timezone as _tz

    co.submitted_at = _dt.now(_tz.utc)
    db.commit()
    db.refresh(co)
    from app.services.notify import notify_owner_change_order
    notify_owner_change_order(db, co)  # best-effort
    return co_read(co)


@router.post("/{co_id}/void")
def void_co(
    project_id: UUID,
    co_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    assert_project_access(db, user, project_id)
    co = _get(db, project_id, co_id)
    co.status = "void"
    db.commit()
    db.refresh(co)
    return co_read(co)


@router.post("/{co_id}/pdf")
def change_order_pdf(
    project_id: UUID,
    co_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Render the change order to a branded PDF, store it in R2 as a Document,
    and return a short-lived download URL."""
    from app.models.client import Client
    from app.models.document import Document
    from app.models.project import Project
    from app.services.change_order_pdf import render_change_order_pdf
    from app.services.r2 import presign_get, put_bytes

    assert_project_access(db, user, project_id)
    co = _get(db, project_id, co_id)

    project = db.get(Project, project_id)
    client = (
        db.get(Client, project.client_id)
        if project is not None and project.client_id is not None
        else None
    )
    pdf = render_change_order_pdf(
        co=co_read(co),
        project={
            "name": getattr(project, "name", None),
            "address": getattr(project, "address", None),
        },
        client={
            "name": getattr(client, "name", None),
            "email": getattr(client, "email", None),
            "phone": getattr(client, "phone", None),
        },
    )

    key = f"change-orders/{co.id}/CO-{co.number}.pdf"
    put_bytes(key, pdf, "application/pdf")

    # Deterministic key, so every regeneration overwrites the same object.
    # Reuse the Document row rather than inserting one per click — the invoice
    # renderer shipped without this and left five rows pointing at one file.
    filename = f"CO-{co.number} - {co.title}.pdf"[:180]
    doc = (
        db.query(Document)
        .filter(Document.r2_key == key)
        .order_by(Document.created_at.desc())
        .first()
    )
    if doc is None:
        doc = Document(
            project_id=project_id,
            category="change_order_pdf",
            phase="in_progress",
            filename=filename,
            r2_key=key,
            mime="application/pdf",
            size_bytes=len(pdf),
            uploaded_by_id=real_user_id(user),
        )
        db.add(doc)
    else:
        doc.filename = filename
        doc.size_bytes = len(pdf)
    db.commit()
    db.refresh(doc)

    return {
        "document_id": str(doc.id),
        "filename": filename,
        "size_bytes": len(pdf),
        "download_url": presign_get(key),
    }
