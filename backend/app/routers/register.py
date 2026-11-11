"""The register — read-only. Phase 1 writes nothing, anywhere.

Scope is always resolved from the caller's own access, never from the request
body. A register is the first surface that reads ACROSS projects, so it is the
first place a scoping mistake shows up as a leak instead of a 403.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import require_office, CurrentUser, accessible_project_ids, assert_project_access, get_current_user
from app.db import get_db
from app.services.register import build_register, register_summary

# Office only (rollout part 3, 2026-10-15): signed in as the test CREW account,
# these answered with money (contract, owing, estimate costs and margins,
# invoices, rates). The field pages never call them.
router = APIRouter(prefix="/register", tags=["register"], dependencies=[Depends(require_office)])


@router.get("")
def read_register(
    project_id: UUID | None = Query(None, description="one project, or omit for everything you can see"),
    severity: str | None = Query(None, pattern="^(blocking|attention)$"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """What needs a person's attention, derived live from existing records.

    Nothing is stored: an item is a view over records that already exist. A
    stored register would be a second copy of a fact that can drift from the
    first, which is the class of bug this codebase has spent real effort
    removing elsewhere.
    """
    if project_id is not None:
        assert_project_access(db, user, project_id)
        pids = [project_id]
    else:
        pids = accessible_project_ids(db, user)

    items = build_register(db, pids, project_id=project_id, company_wide=user.role == "superadmin")
    if severity:
        items = [i for i in items if i["severity"] == severity]

    return {
        "scope": "project" if project_id else "all-accessible",
        "projects_in_scope": len(pids),
        "summary": register_summary(items),
        "items": items,
    }
