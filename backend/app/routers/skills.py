"""Skill engine API — S1 surface.

Public endpoints:
  - GET    /skills                              registry summary
  - GET    /skills/{skill_id}                   per-skill detail incl. recent runs
  - GET    /skill-runs                          filtered run list
  - GET    /skill-runs/{id}                     run detail + diff signals
  - POST   /skill-runs/{id}/corrections         record diff + tag (called from save flows)
  - GET    /skill-runs/by-artifact              find a run by its produced artifact
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_office, require_superadmin
from app.db import get_db
from app.models.ai_job import AIJob
from app.models.skill import CorrectionSignal, SkillRun, SkillVersion
from app.services.skill_engine import (
    KNOWN_CATEGORIES,
    find_run_for_artifact,
    record_corrections,
)


# Superadmin only (Amir, 2026-10-15). Until then every route in both routers checked only
# that someone was signed in, so a manager, a crew member or a client (owner)
# account could read it. Router-level, so a route added later is covered.
# The two feedback routes on skill runs (by-artifact, corrections) stay open to
# the office, so a manager can still judge an AI output where it appears.
router = APIRouter(prefix="/skills", tags=["skills"], dependencies=[Depends(require_superadmin)])
runs_router = APIRouter(prefix="/skill-runs", tags=["skills"])


# ── Pydantic surface ───────────────────────────────────────────────


class SkillVersionRead(BaseModel):
    id: UUID
    skill_id: str
    version: str
    owner_namespace: str
    status: str
    description: str | None
    fingerprint: str | None = None
    prompt_chars: int = 0
    approved_by: str | None = None
    approved_at: Any | None = None
    approval_note: str | None = None
    created_at: Any
    updated_at: Any

    class Config:
        from_attributes = True


def _version_read(v: SkillVersion) -> SkillVersionRead:
    """Build the read model, deriving the prompt fingerprint so the UI can
    show version identity / drift without shipping the full template."""
    from app.services.skill_engine import prompt_fingerprint

    tmpl = v.prompt_template or ""
    return SkillVersionRead(
        id=v.id,
        skill_id=v.skill_id,
        version=v.version,
        owner_namespace=v.owner_namespace,
        status=v.status,
        description=v.description,
        fingerprint=prompt_fingerprint(tmpl) if tmpl else None,
        prompt_chars=len(tmpl),
        approved_by=v.approved_by,
        approved_at=v.approved_at,
        approval_note=v.approval_note,
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


class VersionPromptRead(BaseModel):
    skill_id: str
    version: str
    status: str
    fingerprint: str | None
    prompt_template: str


class SkillSummary(BaseModel):
    skill_id: str
    active_version: SkillVersionRead | None
    all_versions: list[SkillVersionRead]
    run_count: int
    edited_run_count: int
    correction_signal_count: int
    learning_signal_count: int


class SkillRunRead(BaseModel):
    id: UUID
    skill_version_id: UUID
    ai_job_id: UUID | None
    project_type: str | None
    acceptance_status: str
    edit_count: int
    cost_usd: float | None
    accepted_at: Any | None
    created_at: Any


class SkillRunDetail(SkillRunRead):
    input: dict[str, Any]
    raw_output: dict[str, Any] | None
    accepted_output: dict[str, Any] | None
    corrections: list["CorrectionSignalRead"]


class CorrectionSignalRead(BaseModel):
    id: UUID
    path: str
    diff_type: str
    before_value: Any
    after_value: Any
    semantic_category: str | None
    user_note: str | None
    feeds_learning: bool
    created_at: Any

    class Config:
        from_attributes = True


SkillRunDetail.model_rebuild()


class CorrectionsRequest(BaseModel):
    accepted_output: dict[str, Any]
    # One of the categories in skill_engine.KNOWN_CATEGORIES, or null = skipped.
    semantic_category: str | None = None
    user_note: str | None = None


# ── Skill registry ─────────────────────────────────────────────────


@router.get("", response_model=list[SkillSummary])
def list_skills(
    owner_namespace: str = Query("brio-pm"),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> list[SkillSummary]:
    """List every distinct skill with version + telemetry counts."""
    versions = list(
        db.execute(
            select(SkillVersion)
            .where(SkillVersion.owner_namespace == owner_namespace)
            .order_by(SkillVersion.skill_id, desc(SkillVersion.created_at))
        )
        .scalars()
        .all()
    )

    by_skill: dict[str, list[SkillVersion]] = {}
    for v in versions:
        by_skill.setdefault(v.skill_id, []).append(v)

    out: list[SkillSummary] = []
    for skill_id, vlist in by_skill.items():
        version_ids = [v.id for v in vlist]
        active = next((v for v in vlist if v.status == "active"), None)
        run_count = db.execute(
            select(func.count(SkillRun.id)).where(
                SkillRun.skill_version_id.in_(version_ids)
            )
        ).scalar() or 0
        edited_count = len(
            db.execute(
                select(SkillRun.id)
                .where(SkillRun.skill_version_id.in_(version_ids))
                .where(SkillRun.acceptance_status == "edited")
            ).all()
        )
        signal_count = len(
            db.execute(
                select(CorrectionSignal.id)
                .join(SkillRun, SkillRun.id == CorrectionSignal.skill_run_id)
                .where(SkillRun.skill_version_id.in_(version_ids))
            ).all()
        )
        learning_count = len(
            db.execute(
                select(CorrectionSignal.id)
                .join(SkillRun, SkillRun.id == CorrectionSignal.skill_run_id)
                .where(SkillRun.skill_version_id.in_(version_ids))
                .where(CorrectionSignal.feeds_learning.is_(True))
            ).all()
        )
        out.append(
            SkillSummary(
                skill_id=skill_id,
                active_version=(_version_read(active) if active else None),
                all_versions=[_version_read(v) for v in vlist],
                run_count=run_count,
                edited_run_count=edited_count,
                correction_signal_count=signal_count,
                learning_signal_count=learning_count,
            )
        )
    out.sort(key=lambda s: s.skill_id)
    return out


@router.get(
    "/{skill_id}/versions/{version_id}/prompt", response_model=VersionPromptRead
)
def get_version_prompt(
    skill_id: str,
    version_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
) -> VersionPromptRead:
    """Full prompt template for one version — for viewing / diffing in the
    skills admin UI and for confirming what a rollback would restore."""
    from app.services.skill_engine import prompt_fingerprint

    v = db.get(SkillVersion, version_id)
    if v is None or v.skill_id != skill_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="version not found"
        )
    tmpl = v.prompt_template or ""
    return VersionPromptRead(
        skill_id=v.skill_id,
        version=v.version,
        status=v.status,
        fingerprint=prompt_fingerprint(tmpl) if tmpl else None,
        prompt_template=tmpl,
    )


class ApprovalRequest(BaseModel):
    note: str | None = None


@router.post(
    "/{skill_id}/versions/{version_id}/approve", response_model=SkillVersionRead
)
def approve_skill_version(
    skill_id: str,
    version_id: UUID,
    body: ApprovalRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SkillVersionRead:
    """Promote a draft candidate to active. The one explicit human gate —
    no other path activates a prompt version."""
    from app.services.skill_engine import approve_version

    v = db.get(SkillVersion, version_id)
    if v is None or v.skill_id != skill_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="version not found"
        )
    try:
        row = approve_version(db, version_id, approver=user.email, note=body.note)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    db.commit()
    return _version_read(row)


@router.post(
    "/{skill_id}/versions/{version_id}/reject", response_model=SkillVersionRead
)
def reject_skill_version(
    skill_id: str,
    version_id: UUID,
    body: ApprovalRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SkillVersionRead:
    """Reject a draft candidate so it can never become active."""
    from app.services.skill_engine import reject_version

    v = db.get(SkillVersion, version_id)
    if v is None or v.skill_id != skill_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="version not found"
        )
    try:
        row = reject_version(db, version_id, approver=user.email, note=body.note)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    db.commit()
    return _version_read(row)


# ── Run telemetry ──────────────────────────────────────────────────


@runs_router.get("", response_model=list[SkillRunRead])
def list_skill_runs(
    skill_id: str | None = Query(None),
    project_type: str | None = Query(None),
    acceptance_status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_superadmin),
) -> list[SkillRun]:
    stmt = select(SkillRun).order_by(desc(SkillRun.created_at)).limit(limit)
    if skill_id is not None:
        stmt = stmt.join(SkillVersion, SkillVersion.id == SkillRun.skill_version_id).where(
            SkillVersion.skill_id == skill_id
        )
    if project_type is not None:
        stmt = stmt.where(SkillRun.project_type == project_type)
    if acceptance_status is not None:
        stmt = stmt.where(SkillRun.acceptance_status == acceptance_status)
    return list(db.execute(stmt).scalars().all())


@runs_router.get("/{run_id}", response_model=SkillRunDetail)
def get_skill_run(
    run_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_superadmin),
) -> dict:
    run = db.get(SkillRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    corrections = list(
        db.execute(
            select(CorrectionSignal)
            .where(CorrectionSignal.skill_run_id == run.id)
            .order_by(CorrectionSignal.created_at)
        )
        .scalars()
        .all()
    )
    return {
        "id": run.id,
        "skill_version_id": run.skill_version_id,
        "ai_job_id": run.ai_job_id,
        "project_type": run.project_type,
        "acceptance_status": run.acceptance_status,
        "edit_count": run.edit_count,
        "cost_usd": float(run.cost_usd) if run.cost_usd is not None else None,
        "accepted_at": run.accepted_at,
        "created_at": run.created_at,
        "input": run.input or {},
        "raw_output": run.raw_output,
        "accepted_output": run.accepted_output,
        "corrections": [CorrectionSignalRead.model_validate(c) for c in corrections],
    }


@runs_router.post("/{run_id}/corrections")
def post_corrections(
    run_id: UUID,
    body: CorrectionsRequest,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_office),
) -> dict:
    """Record the diff between the run's raw_output and the user's
    accepted_output. Called from save flows after the artifact (scope /
    estimate / etc.) has been written. If `semantic_category` is set it
    must be one of the documented values; only `ai_miss_*` categories
    feed learning."""
    if body.semantic_category is not None and body.semantic_category not in KNOWN_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unknown semantic_category {body.semantic_category!r}. "
                f"Allowed: {sorted(KNOWN_CATEGORIES)}"
            ),
        )
    try:
        run, signal_count = record_corrections(
            db,
            run_id,
            body.accepted_output,
            semantic_category=body.semantic_category,
            user_note=body.user_note,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    db.commit()
    return {
        "skill_run_id": str(run.id),
        "signal_count": signal_count,
        "acceptance_status": run.acceptance_status,
        "feeds_learning": body.semantic_category in KNOWN_CATEGORIES
        and body.semantic_category and body.semantic_category.startswith("ai_miss_"),
    }


@runs_router.get("/by-artifact/{skill_id}/{artifact_marker}/{artifact_value}")
def get_run_by_artifact(
    skill_id: str,
    artifact_marker: str,
    artifact_value: str,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_office),
) -> dict | None:
    """Resolve the skill_run that produced a given artifact. Used by save
    flows that have a scope_id / estimate_version_id / etc. but don't
    know the underlying run."""
    run = find_run_for_artifact(
        db,
        skill_id=skill_id,
        artifact_marker=artifact_marker,
        artifact_value=artifact_value,
    )
    if run is None:
        return None
    return {
        "skill_run_id": str(run.id),
        "skill_version_id": str(run.skill_version_id),
        "acceptance_status": run.acceptance_status,
    }
