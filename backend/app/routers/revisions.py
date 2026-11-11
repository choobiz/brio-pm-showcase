"""Revision history, and putting a record back.

Read the history of a scope, estimate or change order, and restore any recorded
state. A restore snapshots the current state first, so it is itself undoable —
an undo you cannot undo is how a hurried recovery loses more than the mistake.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office
from app.db import get_db
from app.services.revisions import SNAPSHOTTABLE, actor_uuid, list_revisions, restore

router = APIRouter(prefix="/revisions", tags=["revisions"])


def _project_of(db: Session, table: str, row_id: UUID) -> UUID:
    if table not in SNAPSHOTTABLE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{table} does not keep revisions",
        )
    pid = db.execute(
        _T(f"SELECT project_id FROM {table} WHERE id = :i"), {"i": str(row_id)}
    ).scalar()
    if pid is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="record not found"
        )
    return pid


@router.get("")
def get_history(
    table: str = Query(..., description="scopes | estimates | change_orders | invoices"),
    row_id: UUID = Query(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    project_id = _project_of(db, table, row_id)
    assert_project_access(db, user, project_id)
    return {"table": table, "row_id": str(row_id),
            "revisions": list_revisions(db, table=table, row_id=row_id)}


@router.post("/{revision_id}/restore")
def restore_revision(
    revision_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    row = db.execute(
        _T("SELECT table_name, row_id FROM record_revisions WHERE id = :i"),
        {"i": str(revision_id)},
    ).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="revision not found"
        )
    project_id = _project_of(db, row["table_name"], row["row_id"])
    assert_project_access(db, user, project_id)
    try:
        result = restore(db, revision_id=revision_id, actor_id=actor_uuid(user))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    return result
