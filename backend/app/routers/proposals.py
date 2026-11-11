"""Reviewing a proposed change: see it, take it, or decline it with a reason.

⚠️ OFFICE LEVEL FOR DECISIONS, READ FOR EVERYONE WITH ACCESS. §11's action policy
puts "update a record" at human approval, office level. Reading what has been
proposed is not a decision and is not gated the same way — a field user seeing
that someone proposed a change to their project is information, not authority.

⚠️ EVERY DECISION IS FINAL. There is no un-reject and no un-accept. A decided
proposal stays decided and a new proposal is made instead, so the history reads
as a sequence of decisions rather than one row that changed its mind. That is
what makes "who decided what, and when" answerable at all.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.auth import (
    CurrentUser,
    assert_project_access,
    get_current_user,
    require_office,
)
from app.db import get_db
from app.models.proposal import Proposal
from app.services import proposals as svc

router = APIRouter(prefix="/proposals", tags=["proposals"])


class DecisionRequest(BaseModel):
    #: Required on reject. A rejection with no reason answers three of the four
    #: audit questions and leaves blank the one anybody would ask later.
    reason: str | None = None
    #: Accept only these paths. Omit to take everything applicable — sent
    #: explicitly so unticking one in the review screen means something.
    accept_paths: list[str] | None = None


def _read(p: Proposal, *, verified: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(p.id),
        "project_id": str(p.project_id),
        "target_table": p.target_table,
        "target_id": str(p.target_id),
        "origin": p.origin,
        "ai_job_id": str(p.ai_job_id) if p.ai_job_id else None,
        "proposed_by_id": str(p.proposed_by_id) if p.proposed_by_id else None,
        "summary": p.summary,
        "change_set": p.change_set or [],
        "impact": p.impact or [],
        "status": p.status,
        "decided_by_id": str(p.decided_by_id) if p.decided_by_id else None,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "decision_reason": p.decision_reason,
        "revision_number": p.revision_number,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
    if verified is not None:
        # The change set as it stands against the record RIGHT NOW, not as it
        # was when proposed. A reviewer deciding from the stored copy would be
        # deciding about a document that may have moved.
        out["verified"] = verified
        out["applicable_count"] = sum(1 for c in verified if c.get("applicable"))
        out["stale"] = svc.is_stale(verified)
    return out


@router.get("")
def list_proposals(
    project_id: UUID = Query(...),
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Proposals on a project, newest first.

    Decided ones are included by default and deliberately: the register of what
    was declined is the point of the table, and hiding rejections behind a filter
    makes them invisible exactly when someone is looking for precedent.
    """
    assert_project_access(db, user, project_id)
    stmt = select(Proposal).where(Proposal.project_id == project_id)
    if status_filter:
        stmt = stmt.where(Proposal.status == status_filter)
    rows = db.execute(stmt.order_by(Proposal.created_at.desc())).scalars().all()
    return [_read(p) for p in rows]


def _get(db: Session, user: CurrentUser, proposal_id: UUID) -> Proposal:
    p = db.get(Proposal, proposal_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="proposal not found")
    assert_project_access(db, user, p.project_id)
    return p


@router.get("/{proposal_id}")
def get_proposal(
    proposal_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """One proposal, re-checked against the record as it stands now."""
    p = _get(db, user, proposal_id)
    verified: list[dict[str, Any]] | None
    try:
        verified = svc.recheck(db, p)
    except Exception:  # noqa: BLE001
        # The target may have been deleted. That is worth showing as "cannot be
        # checked" rather than failing the whole read — the decision history is
        # still worth reading even when the record it referred to is gone.
        verified = None
    return _read(p, verified=verified)


@router.post("/{proposal_id}/accept")
def accept_proposal(
    proposal_id: UUID,
    body: DecisionRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    p = _get(db, user, proposal_id)
    try:
        result = svc.accept(
            db, p,
            user_id=getattr(user, "id", None),
            accept_paths=body.accept_paths,
            reason=body.reason,
        )
    except svc.ProposalError as e:
        # 409, not 400: the request was well-formed and the state refused it.
        db.commit()   # persist a `stale` transition if accept() set one
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e))
    db.commit()
    return {**result, "proposal": _read(db.get(Proposal, proposal_id))}


@router.post("/{proposal_id}/reject")
def reject_proposal(
    proposal_id: UUID,
    body: DecisionRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    p = _get(db, user, proposal_id)
    try:
        svc.reject(db, p, user_id=getattr(user, "id", None), reason=body.reason or "")
    except svc.ProposalError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    db.commit()
    return _read(db.get(Proposal, proposal_id))


@router.get("/record/{table}/{row_id}")
def proposals_for_record(
    table: str,
    row_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Everything ever proposed about one record, decided or not.

    This is the rejection audit as a reader would want it: standing in front of
    a scope and asking what has been suggested about it and what was decided.
    """
    rows = db.execute(
        select(Proposal)
        .where(Proposal.target_table == table, Proposal.target_id == row_id)
        .order_by(Proposal.created_at.desc())
    ).scalars().all()
    if not rows:
        return []
    # Access is checked against the records' own project, not a supplied one.
    assert_project_access(db, user, rows[0].project_id)
    return [_read(p) for p in rows]
