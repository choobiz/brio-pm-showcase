"""Review an AI-proposed edit, then apply it.

The worker produces a change set and stops. This is where a person accepts it.
Nothing about that is ceremony: an AI write to a scope or a change order is a
write to a document a client will be shown and asked to sign.

Applying re-checks everything the worker checked. The plan may be minutes or
days old, the record may have been edited by hand since, and a change whose
`before` no longer matches must not land. Re-verification against the CURRENT
document is the whole reason the plan stores `before` at all.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text as _T
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, require_office
from app.db import get_db
from app.models.ai_job import AIJob
from app.services.edit_plan import apply as apply_changes
from app.services.edit_plan import verify
from app.services.editable_records import (  # extracted, not duplicated
    TARGETS,
    _current_document,
    _write,
)
from app.services.revisions import actor_uuid, snapshot
from app.services.estimate_scope import NEW_VERSION_SCOPE_SQL, scope_binds

router = APIRouter(prefix="/ai-edits", tags=["ai-edits"])


class ApplyRequest(BaseModel):
    #: Paths the reviewer accepted. Omit to apply every applicable change.
    #: Sent explicitly so unticking one in the review screen means something.
    accept_paths: list[str] | None = None
    #: Scope only: write the result as the NEXT version and leave this one as
    #: it was. A scope that has been sent or priced is a record of what was
    #: said; changing it by description should add to the history, not rewrite it.
    as_new_version: bool = False
    #: Estimate only: the save step's rate-library decisions,
    #: [{key, write, reason?}] (see services/rate_writeback.settle).
    rate_updates: list[dict[str, Any]] | None = None


def _plan_of(db: Session, job_id: UUID) -> tuple[AIJob, dict[str, Any]]:
    job = db.get(AIJob, job_id)
    if job is None or job.kind != "ai_edit":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="edit job not found")
    plan = (job.output or {}).get("edit_plan")
    if not plan:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"this job produced no edit plan (status: {job.status})",
        )
    return job, plan


@router.get("/{job_id}")
def get_plan(
    job_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    """The plan, RE-CHECKED against the record as it stands right now.

    The stored plan is a snapshot of what was true when the job ran. Showing it
    unchecked would let a reviewer approve a change against a document that has
    since moved.
    """
    job, plan = _plan_of(db, job_id)
    assert_project_access(db, user, job.project_id)

    spec = TARGETS.get(str(plan.get("record_type")))
    if spec is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="unknown record type")
    document = _current_document(db, spec, str(plan["record_id"]))
    rechecked = verify(document, plan.get("changes") or [])
    preview, applied = apply_changes(document, rechecked)

    status_row = db.execute(
        _T(f"SELECT status FROM {spec['table']} WHERE id = :i"),
        {"i": str(plan["record_id"])},
    ).scalar() if spec["locked"] else None

    return {
        **plan,
        "changes": rechecked,
        "applicable_count": len(applied),
        "blocked_count": len(rechecked) - len(applied),
        "before": document,
        "preview": preview,
        "record_status": status_row,
        "locked": bool(status_row and status_row in spec["locked"]),
        "warning": _warning(db, spec, str(plan["record_id"])),
        "job_status": job.status,
    }


def _warning(db: Session, spec: dict[str, Any], record_id: str) -> str | None:
    """A reason to look twice, short of a refusal."""
    if spec["table"] != "estimate_versions":
        return None
    active = db.execute(
        _T("SELECT is_active FROM estimate_versions WHERE id = :i"), {"i": record_id}
    ).scalar()
    if not active:
        return None
    billed = db.execute(
        _T("SELECT count(*) FROM invoices WHERE payload->'sourceEstimate'->>'versionId' = :i"),
        {"i": record_id},
    ).scalar()
    base = "This is the ACTIVE estimate version — invoices bill from it."
    return base + (f" {billed} invoice(s) already reference it." if billed else "")


def planned_document(db: Session, user: CurrentUser, job_id: UUID,
                     accept_paths: list[str] | None) -> dict[str, Any]:
    """The document an apply WOULD write, computed without writing it: the
    save step needs it to list the rate-library changes beforehand."""
    job, plan = _plan_of(db, job_id)
    assert_project_access(db, user, job.project_id)
    spec = TARGETS.get(str(plan.get("record_type")))
    if spec is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="unknown record type")
    document = _current_document(db, spec, str(plan["record_id"]))
    rechecked = verify(document, plan.get("changes") or [])
    if accept_paths is not None:
        for change in rechecked:
            if change.get("path") not in set(accept_paths):
                change["applicable"] = False
    new_document, _applied = apply_changes(document, rechecked)
    return new_document if isinstance(new_document, dict) else {}


def _settle_rates(db: Session, record_type: str, document: Any, body: "ApplyRequest",
                  user: CurrentUser, source: str) -> dict[str, Any] | None:
    if record_type != "estimate_version" or not isinstance(document, dict):
        return None
    from app.services.rate_writeback import DecisionError, settle

    try:
        return settle(db, document, body.rate_updates, source=source, user_email=user.email)
    except DecisionError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.post("/{job_id}/apply")
def apply_plan(
    job_id: UUID,
    body: ApplyRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict[str, Any]:
    job, plan = _plan_of(db, job_id)
    assert_project_access(db, user, job.project_id)

    record_type = str(plan.get("record_type"))
    spec = TARGETS.get(record_type)
    if spec is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="unknown record type")
    record_id = str(plan["record_id"])

    if spec["locked"]:
        current = db.execute(
            _T(f"SELECT status FROM {spec['table']} WHERE id = :i"), {"i": record_id}
        ).scalar()
        if current in spec["locked"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=(
                    f"this {record_type.replace('_', ' ')} is {current} — it has left "
                    "the building and is evidence of what was agreed. Create a new "
                    "version instead of editing this one."
                ),
            )

    document = _current_document(db, spec, record_id)
    rechecked = verify(document, plan.get("changes") or [])

    # Unticking a change in the review screen has to mean something.
    if body.accept_paths is not None:
        accepted = set(body.accept_paths)
        for change in rechecked:
            if change.get("path") not in accepted:
                change["applicable"] = False
                change["problem"] = "not accepted by the reviewer"

    new_document, applied = apply_changes(document, rechecked)
    if not applied:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="nothing left to apply — every change was rejected or is stale",
        )

    rates = _settle_rates(db, record_type, new_document, body, user,
                          source=f"estimate version {record_id} (describe-a-change)")

    if body.as_new_version:
        if record_type == "scope":
            new_id = _new_scope_version(db, job, record_id, new_document,
                                        plan.get("instruction") or "")
        elif record_type == "estimate_version":
            new_id = _new_estimate_version(db, job, record_id, new_document,
                                           plan.get("instruction") or "", user)
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="only a scope or an estimate version can be saved as a new version")
        db.commit()
        return {
            "applied": len(applied),
            "skipped": len(rechecked) - len(applied),
            "applied_paths": [c.get("path") for c in applied],
            "recomputed": {},
            "record_type": record_type,
            "record_id": new_id,
            "new_version": True,
            "rate_library": rates,
        }

    # History BEFORE the write, in this transaction. A rolled-back apply must
    # leave no revision claiming it happened.
    snapshot(
        db, table=spec["table"], row_id=record_id, project_id=job.project_id,
        action="ai_edit", actor_id=actor_uuid(user),
        reason=(plan.get("instruction") or "")[:500] or None,
    )

    recomputed = _write(db, spec, record_id, new_document)
    db.commit()

    return {
        "applied": len(applied),
        "skipped": len(rechecked) - len(applied),
        "applied_paths": [c.get("path") for c in applied],
        "recomputed": recomputed,
        "record_type": record_type,
        "record_id": record_id,
        "rate_library": rates,
    }


def _new_scope_version(db: Session, job: AIJob, from_id: str, payload: Any,
                       instruction: str) -> str:
    """Insert the edited scope as the project's next version. The original row
    is untouched, so no snapshot is needed: it IS the history.

    `_revision` records where it came from, so the page can say "changed from
    v3: add the basement suite" instead of presenting it as a fresh reading.
    """
    from app.models.scope import Scope

    prev = db.get(Scope, UUID(from_id))
    next_version = db.execute(
        _T("SELECT coalesce(max(version), 0) + 1 FROM scopes WHERE project_id = :p"),
        {"p": str(job.project_id)}).scalar()
    body = dict(payload) if isinstance(payload, dict) else {}
    # A new version is written today; it does not inherit the old one's date.
    from app.services.home import today

    body["date"] = today().isoformat()
    body["_revision"] = {"from_version": prev.version if prev else None, "from_id": from_id,
                         "instruction": instruction[:500], "edit_job_id": str(job.id)}
    row = Scope(project_id=job.project_id, version=next_version, payload=body)
    db.add(row)
    db.flush()
    return str(row.id)


def _new_estimate_version(db: Session, job: AIJob, from_id: str, payload: Any,
                          instruction: str, user: CurrentUser) -> str:
    """Insert the edited estimate as the project's next version — an INACTIVE
    draft, parented on the one it came from.

    Not made active: invoices, the schedule of values and the contract read
    the active version, and moving it is the separate, deliberate act of
    "Keep" (activate, with its regeneration preview). The money is re-derived
    with the project's division set, as every estimate write is.
    """
    import json as _json
    from uuid import uuid4

    from app.services.estimate_totals import project_contingency, rollup_payload

    src = db.execute(_T("""
        SELECT project_id, reference, cost_basis, overhead_pct, profit_pct
          FROM estimate_versions WHERE id = :i"""), {"i": from_id}).mappings().first()
    if src is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="estimate version not found")
    db.execute(_T("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": str(src["project_id"])})
    next_ver = db.execute(_T(
        "SELECT coalesce(max(version_number), 0) + 1 FROM estimate_versions WHERE project_id = :p"),
        {"p": str(src["project_id"])}).scalar()
    body = dict(payload) if isinstance(payload, dict) else {}
    rollup_payload(body, project_contingency(db, src["project_id"]))
    prev = db.execute(_T("SELECT version_number FROM estimate_versions WHERE id = :i"),
                      {"i": from_id}).scalar()
    body["_revision"] = {"from_version": prev, "from_id": from_id,
                         "instruction": instruction[:500], "edit_job_id": str(job.id)}
    new_id = uuid4()
    db.execute(_T("""
        INSERT INTO estimate_versions
            (id, project_id, version_number, reference, label, cost_basis, overhead_pct,
             profit_pct, notes, payload, parent_version_id, is_active, created_by_id, scope_id)
        VALUES (:id, :p, :v, :ref, :label, :cb, :oh, :pr, :notes, CAST(:payload AS jsonb),
                :parent, false, :uid, """ + NEW_VERSION_SCOPE_SQL + """)
    """), {
        **scope_binds(src["project_id"], from_id),
        "id": new_id, "p": str(src["project_id"]), "v": int(next_ver),
        "ref": src["reference"], "label": f"v{int(next_ver):03d} — changed from v{prev}",
        "cb": src["cost_basis"], "oh": src["overhead_pct"], "pr": src["profit_pct"],
        "notes": f"Changed from v{prev} by description: {instruction[:300]}",
        "payload": _json.dumps(body, default=str), "parent": from_id,
        "uid": user.id if getattr(user, "id", None) else None,
    })
    return str(new_id)
