"""Home (cross-project) and the project road to an estimate. Read-only.

Scope comes from the caller's own access, never from the request: Home is a
cross-project read, which is where a scoping mistake becomes a leak.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import CurrentUser, accessible_project_ids, assert_project_access, require_office
from app.db import get_db
from app.services import home as H
from app.services.register import ATTENTION, BLOCKING, build_register

router = APIRouter(tags=["home"])


@router.get("/home")
def read_home(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    from app.routers.manager import action_queue

    pids = accessible_project_ids(db, user)
    spids = [str(p) for p in pids]
    on = H.today()
    register = build_register(db, pids, company_wide=user.role == "superadmin")
    # Client-side asks (a selection to confirm, a message to answer, a change
    # order to send) are things that need YOU just as much as a closed gate.
    # action_queue scopes itself the same way (superadmin = all live projects).
    asks = action_queue(db=db, user=user)["items"]
    return {
        "today": str(on),
        "needs_you": {
            "register": [i for i in register if i["severity"] == BLOCKING],
            "client": asks,
        },
        "worth_a_look": [i for i in register if i["severity"] == ATTENTION],
        "active_jobs": H.active_jobs(db, spids, on),
        "active_rule": (f"Under way by status, or anything done on it in the last "
                        f"{H.ACTIVE_DAYS} days."),
        "this_week": H.this_week(db, spids, on),
    }


@router.get("/projects/{project_id}/road")
def read_road(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    assert_project_access(db, user, project_id)
    return H.road(db, project_id)
