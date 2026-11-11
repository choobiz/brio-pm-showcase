"""Check an estimate's lines against the rate library.

Read-only and changes nothing. It reports what each line IMPLIES as an hourly
rate and whether the library agrees — see services/line_rates.py for why that is
deliberately not called provenance.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.auth import require_office, CurrentUser, assert_project_access, get_current_user
from app.db import get_db
from app.services.line_rates import check_estimate

# Office only (rollout part 3, 2026-10-15): signed in as the test CREW account,
# these answered with money (contract, owing, estimate costs and margins,
# invoices, rates). The field pages never call them.
router = APIRouter(prefix="/line-rates", tags=["line-rates"], dependencies=[Depends(require_office)])


@router.get("/estimate-version/{version_id}")
def check_estimate_version(
    version_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    row = db.execute(
        _T("""SELECT ev.project_id, ev.reference, ev.payload, p.type AS project_type
                FROM estimate_versions ev
                JOIN projects p ON p.id = ev.project_id
               WHERE ev.id = :i"""),
        {"i": str(version_id)},
    ).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="estimate version not found")
    assert_project_access(db, user, row["project_id"])

    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload or "{}")

    ptype = (row["project_type"] or "").lower()
    rl = "commercial" if "commercial" in ptype else ("residential" if "residential" in ptype else "any")

    out = check_estimate(db, payload or {}, project_type=rl)
    out["reference"] = row["reference"]
    return out
