"""Which documents feed the scope, and which feed the quantity takeoff.

One table, two decisions per row, made by a person and recorded. Replaces the
`category == 'drawing'` filter that used to make this decision invisibly in two
different places. See services/document_selection.py for why.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office, real_user_id
from app.db import get_db
from app.services.document_selection import (
    accuracy_for_selection,
    list_for_project,
)

router = APIRouter(
    prefix="/projects/{project_id}/document-selection", tags=["document-selection"]
)


class SelectionEntry(BaseModel):
    document_id: UUID
    use_for_scope: bool = False
    use_for_quantities: bool = False


class SelectionUpdate(BaseModel):
    #: The COMPLETE selection for this project. Sent whole rather than as a
    #: patch: the screen is a single decision about the whole document set, and
    #: a partial update makes "unticked" indistinguishable from "not mentioned".
    entries: list[SelectionEntry]


@router.get("")
def get_selection(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    documents = list_for_project(db, project_id)
    return {
        "documents": documents,
        **accuracy_for_selection(db, project_id),
        # True once anyone has saved a selection. Until then the consumers fall
        # back to their old behaviour, so existing projects do not silently
        # start estimating from nothing.
        "has_selection": any(
            d["use_for_scope"] is not None or d["use_for_quantities"] is not None
            for d in documents
        ),
    }


@router.put("")
def put_selection(
    project_id: UUID,
    body: SelectionUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)

    valid = {
        str(r[0])
        for r in db.execute(
            _T("SELECT id FROM documents WHERE project_id = :p AND archived = false"),
            {"p": str(project_id)},
        ).all()
    }
    unknown = [str(e.document_id) for e in body.entries if str(e.document_id) not in valid]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{len(unknown)} document(s) do not belong to this project or are archived",
        )

    actor = real_user_id(user)

    # Replace wholesale inside one transaction. A row is kept only when it says
    # something — an entry with both flags false is the same as no entry, and
    # storing it would grow the table by one row per document on every save.
    db.execute(
        _T("DELETE FROM document_selections WHERE project_id = :p"), {"p": str(project_id)}
    )
    kept = 0
    for e in body.entries:
        if not (e.use_for_scope or e.use_for_quantities):
            continue
        db.execute(
            _T(
                "INSERT INTO document_selections "
                "(project_id, document_id, use_for_scope, use_for_quantities, updated_by_id) "
                "VALUES (:p, :d, :s, :q, :u)"
            ),
            {
                "p": str(project_id),
                "d": str(e.document_id),
                "s": e.use_for_scope,
                "q": e.use_for_quantities,
                "u": str(actor) if actor else None,
            },
        )
        kept += 1
    db.commit()

    return {
        "saved": kept,
        "documents": list_for_project(db, project_id),
        **accuracy_for_selection(db, project_id),
        "has_selection": True,
    }
