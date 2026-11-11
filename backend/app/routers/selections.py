"""Client selections & approvals — office side (manager/superadmin).

The office defines material/finish selections (or approval items); the client
decides in the owner portal. Mirrors the change-order office pattern.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office, real_user_id
from app.db import get_db
from app.models.client_selection import (
    ClientSelection,
    SELECTION_DECISION_TYPES,
    SELECTION_PURCHASERS,
    SELECTION_STATUSES,
)


router = APIRouter(prefix="/projects/{project_id}/selections", tags=["selections"])


class SelectionCreate(BaseModel):
    title: str
    category: str | None = None
    description: str | None = None
    decision_type: str = "select"
    purchaser: str | None = None
    options: list[dict] = []
    due_date: str | None = None  # ISO 'YYYY-MM-DD'
    display_order: int = 0
    activity_code: str | None = None
    lead_time_days: int | None = None
    allowance: float | None = None


class SelectionUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    description: str | None = None
    decision_type: str | None = None
    purchaser: str | None = None
    options: list[dict] | None = None
    due_date: str | None = None
    display_order: int | None = None
    status: str | None = None  # office may reopen to 'pending'; 'confirmed' goes through /confirm
    activity_code: str | None = None
    lead_time_days: int | None = None
    allowance: float | None = None


def _norm_options(options: list[dict] | None) -> list[dict]:
    """Give every option a stable id and clean shape."""
    out: list[dict] = []
    for i, o in enumerate(options or [], 1):
        if not isinstance(o, dict):
            continue
        label = str(o.get("label") or "").strip()
        if not label:
            continue
        pd = o.get("price_delta")
        out.append({
            "id": str(o.get("id") or f"opt-{i}"),
            "label": label,
            "description": (str(o.get("description")).strip() or None) if o.get("description") else None,
            "price_delta": float(pd) if pd not in (None, "") else None,
        })
    return out


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def sel_read(s: ClientSelection) -> dict[str, Any]:
    opts = s.options or []
    selected = next((o for o in opts if o.get("id") == s.selected_option_id), None)
    return {
        "id": str(s.id),
        "title": s.title,
        "category": s.category,
        "description": s.description,
        "decision_type": s.decision_type,
        "purchaser": s.purchaser,
        "options": opts,
        "status": s.status,
        "selected_option_id": s.selected_option_id,
        "selected_option": selected,
        "client_comment": s.client_comment,
        "decided_at": s.decided_at.isoformat() if s.decided_at else None,
        "due_date": s.due_date.isoformat() if s.due_date else None,
        "display_order": s.display_order,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "activity_code": s.activity_code,
        "lead_time_days": s.lead_time_days,
        "allowance": float(s.allowance) if s.allowance is not None else None,
        "sent_at": s.sent_at.isoformat() if s.sent_at else None,
        "change_order_id": str(s.change_order_id) if s.change_order_id else None,
    }


def _get(db: Session, project_id: UUID, sid: UUID) -> ClientSelection:
    s = db.get(ClientSelection, sid)
    if s is None or s.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="selection not found")
    return s


def _uid(user: CurrentUser) -> UUID | None:
    return real_user_id(user)


@router.get("")
def list_selections(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> list[dict]:
    assert_project_access(db, user, project_id)
    rows = db.execute(
        select(ClientSelection).where(ClientSelection.project_id == project_id)
        .order_by(ClientSelection.display_order, ClientSelection.created_at)
    ).scalars().all()
    return [sel_read(s) for s in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_selection(
    project_id: UUID,
    body: SelectionCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    assert_project_access(db, user, project_id)
    dtype = body.decision_type if body.decision_type in SELECTION_DECISION_TYPES else "select"
    purch = body.purchaser if body.purchaser in SELECTION_PURCHASERS else None
    s = ClientSelection(
        project_id=project_id, title=body.title, category=body.category,
        description=body.description, decision_type=dtype, purchaser=purch,
        options=_norm_options(body.options), status="pending",
        due_date=_parse_date(body.due_date), display_order=body.display_order,
        created_by_id=_uid(user), activity_code=body.activity_code or None,
        lead_time_days=body.lead_time_days,
        allowance=Decimal(str(body.allowance)) if body.allowance is not None else None,
    )
    # A draft until "Send to client" (R2Selections): the client sees only sent ones.
    db.add(s)
    db.commit()
    db.refresh(s)
    return sel_read(s)


@router.patch("/{sid}")
def update_selection(
    project_id: UUID,
    sid: UUID,
    body: SelectionUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    assert_project_access(db, user, project_id)
    s = _get(db, project_id, sid)
    if body.title is not None:
        s.title = body.title
    if body.category is not None:
        s.category = body.category
    if body.description is not None:
        s.description = body.description
    if body.decision_type is not None and body.decision_type in SELECTION_DECISION_TYPES:
        s.decision_type = body.decision_type
    if body.purchaser is not None:
        s.purchaser = body.purchaser if body.purchaser in SELECTION_PURCHASERS else None
    if body.options is not None:
        s.options = _norm_options(body.options)
    if body.due_date is not None:
        s.due_date = _parse_date(body.due_date)
    if body.display_order is not None:
        s.display_order = body.display_order
    if body.activity_code is not None:
        s.activity_code = body.activity_code or None
    if body.lead_time_days is not None:
        s.lead_time_days = body.lead_time_days
    if body.allowance is not None:
        s.allowance = Decimal(str(body.allowance))
    if body.status == "pending":
        s.status = "pending"
    elif body.status == "confirmed":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Use Confirm, which also drafts any change order.")
    db.commit()
    db.refresh(s)
    return sel_read(s)


@router.delete("/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_selection(
    project_id: UUID,
    sid: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
):
    assert_project_access(db, user, project_id)
    s = _get(db, project_id, sid)
    db.delete(s)
    db.commit()


# ── Planned against the schedule (R2Selections, 2026-11-02) ──────────────
@router.get("/plan")
def selection_plan(project_id: UUID, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_office)) -> dict:
    assert_project_access(db, user, project_id)
    from app.services.selection_plan import plan
    return plan(db, project_id)


@router.get("/suggestions")
def selection_suggestions(project_id: UUID, db: Session = Depends(get_db),
                          user: CurrentUser = Depends(require_office)) -> list[dict]:
    assert_project_access(db, user, project_id)
    from app.services.selection_plan import suggestions
    return suggestions(db, project_id)


@router.post("/{sid}/send")
def send_selection(project_id: UUID, sid: UUID, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_office)) -> dict:
    assert_project_access(db, user, project_id)
    from app.services import selection_plan as sp
    s = _get(db, project_id, sid)
    try:
        sp.send(db, project_id, s)
    except sp.SelectionError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    db.commit()
    db.refresh(s)
    from app.services.notify import notify_owner_selection
    notify_owner_selection(db, project_id, title=s.title, due_date=s.due_date)  # best-effort
    return sel_read(s)


@router.post("/{sid}/confirm")
def confirm_selection(project_id: UUID, sid: UUID, db: Session = Depends(get_db),
                      user: CurrentUser = Depends(require_office)) -> dict:
    assert_project_access(db, user, project_id)
    from app.services import selection_plan as sp
    s = _get(db, project_id, sid)
    try:
        co = sp.confirm(db, project_id, s, _uid(user))
    except sp.SelectionError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    db.commit()
    db.refresh(s)
    out = sel_read(s)
    out["change_order"] = ({"id": str(co.id), "number": co.number, "cost_impact": float(co.cost_impact)}
                           if co else None)
    return out
