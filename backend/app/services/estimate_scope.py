"""Which scope an estimate version was priced from (estimate_versions.scope_id).

Set when a version is created: a generated version takes the scope its job
was given, a revision inherits its parent's. Migration 0081 backfilled the
existing rows the same way. Used by the "scope + selections + lump sum" PDF to
put the estimate's own scope in front of it (Amir, 2026-10-27: on 456 W
Sample the corridor estimate already belongs to the corridor scope; asking
which scope to include was a question the data could answer).
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

#: SQL for the scope_id of a new version. Binds :scope_hint (the job's scope id,
#: or None), :scope_pid (the project) and :scope_parent (the parent version, or
#: None). A hinted scope must exist on the same project; otherwise the parent's.
NEW_VERSION_SCOPE_SQL = (
    "COALESCE((SELECT s.id FROM scopes s WHERE s.id::text = CAST(:scope_hint AS text) "
    "AND s.project_id = CAST(:scope_pid AS uuid)), "
    "(SELECT pv.scope_id FROM estimate_versions pv WHERE pv.id = CAST(:scope_parent AS uuid)))"
)


def scope_binds(project_id: Any, parent_id: Any = None, scope_hint: Any = None) -> dict[str, Any]:
    return {"scope_hint": str(scope_hint) if scope_hint else None,
            "scope_pid": str(project_id),
            "scope_parent": str(parent_id) if parent_id else None}


def scope_for_pdf(db, project_id: UUID, version_id: UUID) -> tuple[Any | None, int]:
    """(scope row, number of scopes on the project) for this estimate's PDF.

    The version's own scope when it has one; else the project's only scope;
    else None, and the caller asks. Never the latest of several: that printed
    the corridor scope on the suite estimate."""
    scopes = db.execute(
        text("SELECT id, version, payload, created_at FROM scopes WHERE project_id = :p ORDER BY version DESC"),
        {"p": str(project_id)}).all()
    own = db.execute(text("SELECT scope_id FROM estimate_versions WHERE id = :v AND project_id = :p"),
                     {"v": str(version_id), "p": str(project_id)}).scalar()
    if own is not None:
        hit = [s for s in scopes if str(s[0]) == str(own)]
        if hit:
            return hit[0], len(scopes)
    if len(scopes) == 1:
        return scopes[0], 1
    return None, len(scopes)
