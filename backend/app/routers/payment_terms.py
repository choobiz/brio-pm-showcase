"""Payment-terms templates + per-project payment schedule.

Applying a schedule writes into the latest contract's payload.paymentSchedule,
which the contract/package/cashflow PDFs render (see services/package_pdf.py).
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office, real_user_id
from app.db import get_db
from app.models.payment_term_template import PaymentTermTemplate


router = APIRouter(tags=["payment-terms"])


def _uid(user: CurrentUser) -> UUID | None:
    return real_user_id(user)


def _norm(entries: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for e in entries or []:
        m = (e.get("milestone") or "").strip()
        try:
            pct = round(float(e.get("pct")), 2)
        except (TypeError, ValueError):
            continue
        if not m:
            continue
        out.append({"milestone": m, "pct": pct, "detail": (e.get("detail") or "").strip()})
    return out


def _tpl(t: PaymentTermTemplate) -> dict:
    return {"id": str(t.id), "name": t.name, "description": t.description,
            "is_builtin": t.is_builtin, "entries": t.entries or []}


# ---------------- templates ----------------
class TemplateIn(BaseModel):
    name: str
    description: str | None = None
    entries: list[dict]


@router.get("/payment-terms/templates")
def list_templates(db: Session = Depends(get_db), user: CurrentUser = Depends(require_office)) -> list[dict]:
    rows = db.execute(
        select(PaymentTermTemplate).order_by(
            PaymentTermTemplate.is_builtin.desc(), PaymentTermTemplate.name)
    ).scalars().all()
    return [_tpl(t) for t in rows]


@router.post("/payment-terms/templates", status_code=status.HTTP_201_CREATED)
def save_template(body: TemplateIn, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(require_office)) -> dict:
    entries = _norm(body.entries)
    if not entries:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="at least one entry is required")
    t = PaymentTermTemplate(name=(body.name or "").strip() or "Custom",
                            description=(body.description or "").strip() or None,
                            is_builtin=False, entries=entries, created_by_id=_uid(user))
    db.add(t)
    db.commit()
    db.refresh(t)
    return _tpl(t)


@router.delete("/payment-terms/templates/{tid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(tid: UUID, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require_office)):
    t = db.get(PaymentTermTemplate, tid)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="template not found")
    if t.is_builtin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="built-in templates cannot be deleted")
    db.delete(t)
    db.commit()


# ---------------- per-project schedule ----------------
class TermsIn(BaseModel):
    entries: list[dict]


@router.get("/projects/{project_id}/payment-terms")
def get_terms(project_id: UUID, db: Session = Depends(get_db),
              user: CurrentUser = Depends(require_office)) -> dict:
    assert_project_access(db, user, project_id)
    row = db.execute(text("select payload from contracts where project_id=:p "
                          "order by version_number desc limit 1"), {"p": project_id}).scalar()
    sched = row.get("paymentSchedule") if isinstance(row, dict) else None
    return {"has_contract": row is not None, "entries": _norm(sched)}


@router.put("/projects/{project_id}/payment-terms")
def set_terms(project_id: UUID, body: TermsIn, db: Session = Depends(get_db),
              user: CurrentUser = Depends(require_office)) -> dict:
    assert_project_access(db, user, project_id)
    entries = _norm(body.entries)
    if not entries:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="at least one entry is required")
    row = db.execute(text("select id, payload from contracts where project_id=:p "
                          "order by version_number desc limit 1"), {"p": project_id}).first()
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="no contract for this project — generate the contract first")
    payload = row.payload if isinstance(row.payload, dict) else {}
    payload["paymentSchedule"] = entries
    db.execute(text("update contracts set payload=CAST(:pl AS jsonb) where id=:c"),
               {"pl": json.dumps(payload), "c": row.id})
    db.commit()
    return {"ok": True, "sum": round(sum(e["pct"] for e in entries), 2), "entries": entries}
