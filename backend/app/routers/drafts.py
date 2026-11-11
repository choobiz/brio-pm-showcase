"""What has been drafted and not issued, and taking one back.

Reading is for anyone with project access — knowing what is sitting unissued on
your own job is information. Removing one is office level: it deletes a record,
and §11's action policy puts anything that changes a record behind human
approval at office level.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import (
    CurrentUser,
    assert_project_access,
    get_current_user,
    require_office,
)
from app.db import get_db
from app.services import drafts as svc

router = APIRouter(prefix="/drafts", tags=["drafts"])


class DiscardRequest(BaseModel):
    #: Optional, but it lands on the snapshot so the discard is explicable later.
    reason: str | None = None


@router.get("")
def list_drafts(
    project_id: UUID = Query(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    return svc.list_drafts(db, project_id)


@router.delete("/{kind}/{draft_id}")
def discard_draft(
    kind: str,
    draft_id: UUID,
    body: DiscardRequest | None = None,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    """Discard a draft. Refuses anything issued, or anything depended upon."""
    from sqlalchemy import text as _T

    spec = svc.DRAFTABLE.get(kind)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown kind")
    # Checked against the row's own project before anything is touched; until
    # 2026-10-12 any office user could discard any project's draft.
    pid = db.execute(_T(f"SELECT project_id FROM {spec['table']} WHERE id = :i"),
                     {"i": str(draft_id)}).scalar()
    if pid is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="draft not found")
    assert_project_access(db, user, pid)
    try:
        out = svc.delete_draft(
            db, kind, draft_id,
            user_id=getattr(user, "id", None),
            reason=(body.reason if body else None),
        )
    except svc.DraftError as e:
        # 409: the request was well-formed; the state refused it.
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(e))
    db.commit()
    return out
