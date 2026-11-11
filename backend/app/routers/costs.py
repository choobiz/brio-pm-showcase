"""Money › Costs: bills BRIO receives, and the budget per division.

Office only (bills and budgets are money). See services/costs.py for the rules.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office
from app.db import get_db
from app.services import costs as svc

router = APIRouter(prefix="/projects/{project_id}/costs", tags=["costs"])


class BillIn(BaseModel):
    kind: Optional[Literal["supplier_invoice", "subtrade_invoice", "other"]] = None
    vendor: Optional[str] = None
    invoice_number: Optional[str] = None
    cost_date: Optional[date] = None
    amount: Optional[float] = None
    gst: Optional[float] = None
    division: Optional[str] = None
    description: Optional[str] = None
    source_document_id: Optional[UUID] = None


class BudgetIn(BaseModel):
    estimate_version_id: UUID
    mapping: dict[str, str] = {}
    co_divisions: dict[str, str] = {}


def _uid(user: CurrentUser) -> UUID | None:
    try:
        return UUID(str(user.id))
    except (TypeError, ValueError):
        return None


def _guard(db: Session, user: CurrentUser, project_id: UUID) -> None:
    assert_project_access(db, user, project_id)


def _fail(e: svc.CostError) -> HTTPException:
    code = status.HTTP_409_CONFLICT if isinstance(e, svc.Duplicate) else status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(code, str(e))


@router.get("")
def get_costs(project_id: UUID, db: Session = Depends(get_db),
              user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    _guard(db, user, project_id)
    return svc.summary(db, project_id)


@router.post("", status_code=status.HTTP_201_CREATED)
def add_bill(project_id: UUID, body: BillIn, db: Session = Depends(get_db),
             user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    _guard(db, user, project_id)
    try:
        out = svc.create(db, project_id, body.model_dump(), _uid(user))
    except svc.CostError as e:
        raise _fail(e) from e
    db.commit()
    return out


@router.get("/budget/estimates")
def budget_estimates(project_id: UUID, db: Session = Depends(get_db),
                     user: CurrentUser = Depends(require_office)) -> list[dict[str, Any]]:
    _guard(db, user, project_id)
    return svc.estimates_for_budget(db, project_id)


@router.post("/budget/preview")
def preview_budget(project_id: UUID, body: BudgetIn, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """What the budget would be. Writes nothing."""
    _guard(db, user, project_id)
    try:
        return svc.budget_preview(db, project_id, body.estimate_version_id, body.mapping, body.co_divisions)
    except svc.CostError as e:
        raise _fail(e) from e


@router.put("/budget")
def save_budget(project_id: UUID, body: BudgetIn, db: Session = Depends(get_db),
                user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    _guard(db, user, project_id)
    try:
        out = svc.set_budget(db, project_id, body.estimate_version_id, body.mapping, body.co_divisions, _uid(user))
    except svc.CostError as e:
        raise _fail(e) from e
    db.commit()
    return out


# After /budget: a PUT /{cost_id} registered first would swallow PUT /budget.
@router.put("/{cost_id}")
def edit_bill(project_id: UUID, cost_id: UUID, body: BillIn, db: Session = Depends(get_db),
              user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    _guard(db, user, project_id)
    try:
        out = svc.update(db, project_id, cost_id, body.model_dump(exclude_unset=True), _uid(user))
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except svc.CostError as e:
        raise _fail(e) from e
    db.commit()
    return out


@router.delete("/{cost_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bill(project_id: UUID, cost_id: UUID, db: Session = Depends(get_db),
                user: CurrentUser = Depends(require_office)) -> Response:
    _guard(db, user, project_id)
    try:
        svc.delete(db, project_id, cost_id, _uid(user))
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
