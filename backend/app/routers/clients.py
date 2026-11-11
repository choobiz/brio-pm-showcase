"""Client / CRM HTTP routes.

Mirrors the JSON registry CLI in ``brio-admin/scripts/contacts.mjs`` —
list/search, detail with rollups, find-or-create dedup, merge, tag mgmt.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_superadmin
from app.db import get_db
from app.models.client import Client
from app.schemas.client import (
    ClientDocumentSummary,
    ClientFindOrCreate,
    ClientFindOrCreateResponse,
    ClientInvoiceSummary,
    ClientListItem,
    ClientPaymentSummary,
    ClientProjectSummary,
    ClientRead,
    ClientReadDetail,
    ClientUpdate,
    TagPayload,
)
from app.services import clients as svc


# Superadmin only (Amir, 2026-10-15). Until then every route here checked only
# that someone was signed in, so a manager, a crew member or a client (owner)
# account could read it. Router-level, so a route added later is covered.
router = APIRouter(prefix="/clients", tags=["clients"], dependencies=[Depends(require_superadmin)])


# ── List / search ─────────────────────────────────────────────────────────


@router.get("", response_model=list[ClientListItem])
def list_clients(
    q: Optional[str] = Query(default=None, description="substring across name/email/phone/address"),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[ClientListItem]:
    rows = svc.search_clients(db, q)
    out: list[ClientListItem] = []
    for c in rows:
        out.append(
            ClientListItem(
                id=c.id,
                name=c.name,
                primary_email=svc.primary_email(c),
                primary_phone=svc.primary_phone(c),
                project_count=svc.project_count(db, c.id),
                last_activity=svc.last_activity_for(db, c.id) or c.last_seen,
                tags=list(c.tags or []),
            )
        )
    return out


# ── Detail ────────────────────────────────────────────────────────────────


@router.get("/{client_id}", response_model=ClientReadDetail)
def get_client(
    client_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> ClientReadDetail:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="client not found")

    rollup = svc.rollup_for_client(db, client_id)
    invoices_by_project: dict[UUID, list] = {}
    for inv in rollup.invoices:
        invoices_by_project.setdefault(inv.project_id, []).append(inv)
    invoice_to_project = {inv.id: inv.project_id for inv in rollup.invoices}

    project_summaries: list[ClientProjectSummary] = []
    for p in rollup.projects:
        billed = sum(
            (svc._invoice_total(inv) for inv in invoices_by_project.get(p.id, [])),
            Decimal("0"),
        )
        paid = sum(
            (pay.amount for pay in rollup.payments if invoice_to_project.get(pay.invoice_id) == p.id),
            Decimal("0"),
        )
        project_summaries.append(
            ClientProjectSummary(
                id=p.id,
                slug=p.slug,
                name=p.name,
                address=p.address,
                status=p.status,
                contract_value=rollup.project_contract_values.get(p.id, Decimal("0")),
                total_billed=billed,
                total_paid=paid,
            )
        )

    detail = ClientReadDetail.model_validate(client, from_attributes=True).model_copy(
        update={
            "projects": project_summaries,
            "documents": [
                ClientDocumentSummary(
                    id=d.id,
                    project_id=d.project_id,
                    category=d.category,
                    filename=d.filename,
                    created_at=d.created_at.date() if d.created_at else None,
                )
                for d in rollup.documents
            ],
            "invoices": [
                ClientInvoiceSummary(
                    id=inv.id,
                    project_id=inv.project_id,
                    number=inv.number,
                    type=inv.type,
                    status=inv.status,
                    total=svc._invoice_total(inv),
                    paid_amount=inv.paid_amount,
                )
                for inv in rollup.invoices
            ],
            "payments": [
                ClientPaymentSummary(
                    id=p.id,
                    invoice_id=p.invoice_id,
                    method=p.method,
                    amount=p.amount,
                    paid_at=p.paid_at.date() if p.paid_at else None,
                )
                for p in rollup.payments
            ],
            "total_billed": rollup.total_billed,
            "total_paid": rollup.total_paid,
            "total_outstanding": rollup.total_outstanding,
            "last_activity": rollup.last_activity,
        }
    )
    return detail


# ── Find-or-create ────────────────────────────────────────────────────────


@router.post(
    "/find-or-create",
    response_model=ClientFindOrCreateResponse,
    status_code=status.HTTP_200_OK,
)
def find_or_create(
    payload: ClientFindOrCreate,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> ClientFindOrCreateResponse:
    if not (payload.name or payload.email or payload.phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one of name / email / phone is required",
        )
    result = svc.find_or_create_client(
        db,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        address=payload.address,
    )
    # This endpoint is the Clients page and the project picker: whoever comes
    # back is a client now, even if they matched a CRM prospect.
    svc.mark_as_client(db, result.client.id)
    db.commit()
    db.refresh(result.client)
    return ClientFindOrCreateResponse(
        client=ClientRead.model_validate(result.client, from_attributes=True),
        created=result.created,
    )


# ── Update ────────────────────────────────────────────────────────────────


@router.patch("/{client_id}", response_model=ClientRead)
def update_client(
    client_id: UUID,
    payload: ClientUpdate,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> ClientRead:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="client not found")

    if payload.name is not None:
        client.name = payload.name
        client.normalized_name = svc.normalize_name(payload.name)
    if payload.notes is not None:
        client.notes = payload.notes
    if payload.tags is not None:
        client.tags = list(payload.tags)

    db.commit()
    db.refresh(client)
    return ClientRead.model_validate(client, from_attributes=True)


# ── Merge ─────────────────────────────────────────────────────────────────


@router.post("/{client_id}/merge", response_model=ClientRead)
def merge(
    client_id: UUID,
    into: UUID = Query(..., description="target client id (the survivor)"),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> ClientRead:
    try:
        target = svc.merge_clients(db, source_id=client_id, target_id=into)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    db.commit()
    db.refresh(target)
    return ClientRead.model_validate(target, from_attributes=True)


# ── Tags ──────────────────────────────────────────────────────────────────


@router.post("/{client_id}/tags", response_model=ClientRead)
def add_client_tag(
    client_id: UUID,
    payload: TagPayload,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> ClientRead:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="client not found")
    svc.add_tag(db, client, payload.tag)
    db.commit()
    db.refresh(client)
    return ClientRead.model_validate(client, from_attributes=True)


@router.delete("/{client_id}/tags/{tag}", response_model=ClientRead)
def remove_client_tag(
    client_id: UUID,
    tag: str,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> ClientRead:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="client not found")
    svc.remove_tag(db, client, tag)
    db.commit()
    db.refresh(client)
    return ClientRead.model_validate(client, from_attributes=True)
