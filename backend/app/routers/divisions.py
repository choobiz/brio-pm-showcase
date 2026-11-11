"""Which cost divisions a project's estimate is organised into.

READ-ONLY, deliberately. The phase plan lists a "division-set admin UI" here,
but the action policy puts COMPANY-WIDE writes — a rate, a division set — in
phase 4 behind an explicit company-scope confirmation. Editing a division set
changes the shape of every future estimate on every project that maps to it,
which is a wider blast radius than anything else this workspace touches.

So phase 2 makes the sets visible and explains which applies and why; phase 4
makes them editable. Seeing them is most of the value: until now the only way to
read a division set was to open a migration.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.auth import require_office, CurrentUser, assert_project_access, get_current_user
from app.db import get_db

# Office only (rollout part 3, 2026-10-15): signed in as the test CREW account,
# these answered with money (contract, owing, estimate costs and margins,
# invoices, rates). The field pages never call them.
router = APIRouter(prefix="/divisions", tags=["divisions"], dependencies=[Depends(require_office)])


@router.get("")
def list_division_sets(
    project_id: UUID | None = Query(None, description="resolve the set this project would use"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Every set, its divisions, and which project types map to it.

    With a project_id, also says which set that project resolves to and why —
    including the case where it resolves to NOTHING, which is the one worth
    saying out loud: an estimate on an unclassified project falls back to a
    generic category list that is not Brio's standard.
    """
    sets = [dict(r) for r in db.execute(_T("""
        SELECT s.id, s.code, s.name, s.standard, s.version, s.source_ref, s.notes,
               (SELECT string_agg(m.project_type, ',' ORDER BY m.project_type)
                  FROM project_type_division_sets m WHERE m.set_id = s.id) AS project_types
          FROM division_sets s WHERE s.is_active ORDER BY s.standard, s.code""")).mappings()]

    for s in sets:
        s["id"] = str(s["id"])
        s["project_types"] = (s["project_types"] or "").split(",") if s["project_types"] else []
        rows = [dict(r) for r in db.execute(_T("""
            SELECT number, title, sequence, contingency_pct, active_by_default, is_reserved
              FROM divisions WHERE set_id = :s ORDER BY sequence"""),
            {"s": s["id"]}).mappings()]
        s["divisions"] = [{
            "number": d["number"],
            "title": d["title"],
            "contingency_pct": float(d["contingency_pct"]),
            "used_by_default": bool(d["active_by_default"]),
            "reserved": bool(d["is_reserved"]),
        } for d in rows]
        s["counts"] = {
            "total": len(rows),
            "usable": sum(1 for d in rows if not d["is_reserved"]),
            "used_by_default": sum(1 for d in rows if d["active_by_default"]),
            "reserved": sum(1 for d in rows if d["is_reserved"]),
        }

    resolved: dict[str, Any] | None = None
    if project_id is not None:
        assert_project_access(db, user, project_id)
        row = db.execute(_T("SELECT name, type FROM projects WHERE id = :p"),
                         {"p": str(project_id)}).mappings().first()
        ptype = row["type"] if row else None
        match = next((s for s in sets if ptype and ptype in s["project_types"]), None)
        resolved = {
            "project_name": row["name"] if row else None,
            "project_type": ptype,
            "set_code": match["code"] if match else None,
            "why": (
                f"This project is {ptype.replace('_', ' ')}, which uses {match['name']}."
                if match else
                "This project has no type set, so an estimate would fall back to a generic "
                "category list that is not Brio's standard. Set the type to fix it."
            ),
        }

    return {
        "sets": sets,
        "resolved": resolved,
        # Said plainly so nobody goes looking for an edit button that is not
        # there, and so the reason is a decision rather than an omission.
        "editable": False,
        "editable_note": (
            "Division sets are read-only for now. Changing one changes the shape of every "
            "future estimate on every project that uses it, so editing comes with the other "
            "company-wide changes rather than here."
        ),
    }
