"""AI jobs router — list, detail, enqueue, SSE stream.

Gating: `POST /ai-jobs` is office-only (manager or superadmin) on a project
the caller is assigned to. It was superadmin-only until 2026-10-15, when Amir
opened AI writing to managers. Scope, estimate and describe-a-change jobs also
need a cost the person saw and approved (`input.approve_usd`), as Ask and
Prepare do; the worker stops before spending more than that.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.auth import (
    CurrentUser, accessible_project_ids, assert_project_access, get_current_user, require_office,
)
from app.db import SessionLocal, get_db
from app.services.job_progress import summarise as summarise_steps
from app.models.ai_job import AIJob
from app.schemas.ai_job import AIJobCreate, AIJobRead


# Office only (rollout part 3, 2026-10-15): signed in as the test CREW account,
# these answered with money (contract, owing, estimate costs and margins,
# invoices, rates). The field pages never call them.
router = APIRouter(prefix="/ai-jobs", tags=["ai_jobs"], dependencies=[Depends(require_office)])

#: Kinds that show a cost and wait for approval before they run (Amir,
#: 2026-10-15: scope, estimate and describe-a-change, like Ask and Prepare).
APPROVAL_KINDS = frozenset({"scope_analysis", "estimate_full", "estimate_generation", "ai_edit"})
#: Same ceiling as a Prepare step: a typo of 500 must not be approvable.
MAX_APPROVE_USD = 50.0


@router.get("/cost-estimate")
def cost_estimate_for_job(
    project_id: UUID,
    kind: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """What a job of this kind will cost at most, shown before it runs."""
    assert_project_access(db, user, project_id)
    from app.services import cost_estimate

    return cost_estimate.ai_job(db, kind)


@router.get("", response_model=list[AIJobRead])
def list_ai_jobs(
    project_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[AIJob]:
    stmt = select(AIJob).order_by(AIJob.created_at.desc())
    # Until 2026-10-12 this returned every job on every project to any signed-in
    # user, inputs and outputs included. Without a project, it is now the
    # caller's own projects only.
    if project_id is not None:
        assert_project_access(db, user, project_id)
        stmt = stmt.where(AIJob.project_id == project_id)
    else:
        stmt = stmt.where(AIJob.project_id.in_(accessible_project_ids(db, user)))
    return list(db.execute(stmt).scalars().all())


@router.get("/{job_id}", response_model=AIJobRead)
def get_ai_job(
    job_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> AIJob:
    job = db.get(AIJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ai_job not found")
    assert_project_access(db, user, job.project_id)
    return job


@router.post(
    "",
    response_model=AIJobRead,
    status_code=status.HTTP_201_CREATED,
)
def create_ai_job(
    payload: AIJobCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> AIJob:
    assert_project_access(db, user, payload.project_id)
    if payload.kind in APPROVAL_KINDS:
        approved = (payload.input or {}).get("approve_usd")
        if not isinstance(approved, (int, float)) or isinstance(approved, bool) \
                or not 0 < approved <= MAX_APPROVE_USD:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Approve the cost first: this job needs the amount you approved (approve_usd).",
            )
    # A pricing job cannot start while a quantity from the drawing takeoff is
    # still unreviewed. Raises 409 naming what is outstanding.
    from app.services.quantity_gate import GATED_JOB_KINDS, assert_gate_closed

    if payload.kind in GATED_JOB_KINDS:
        assert_gate_closed(db, payload.project_id,
                           (payload.input or {}).get("discipline"))
    job = AIJob(
        project_id=payload.project_id,
        kind=payload.kind,
        status="queued",
        input=payload.input,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------
#
# Manual smoke test (with a running API + worker):
#   1. JOB=$(curl -s -X POST http://localhost:8004/ai-jobs \
#         -H 'Content-Type: application/json' \
#         -d '{"project_id":"<uuid>","kind":"email_draft",
#              "input":{"prompt":"Write a 3-line thank you note"}}' \
#       | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
#   2. curl -N http://localhost:8004/ai-jobs/$JOB/stream
#
# Expected: at least one `data: {"status":"running",...}` line, then a final
# `data: {"status":"done",...}` line, then the connection closes.


def _job_event_payload(job: AIJob) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "status": job.status,
        "kind": job.kind,
        "model": job.model,
        "input_tokens": job.input_tokens,
        "output_tokens": job.output_tokens,
        "cost_usd": str(job.cost_usd) if job.cost_usd is not None else "0",
        "attempts": job.attempts,
        "error": job.error,
        "output": job.output,
        # What it is doing right now, not only that it is doing something.
        # estimate_full runs for 375s on average; "running" alone is
        # indistinguishable from "hung", which is why people reload and start a
        # second one.
        "steps": job.steps or [],
        "progress": summarise_steps(job.steps),
    }


async def _stream_job_events(job_id: UUID, poll_seconds: float = 1.0):
    """Yield SSE events on every status change until the job is terminal."""
    last_status: str | None = None
    # Heartbeat / safety cap: stop after ~10 minutes even if no resolution.
    max_iterations = int(10 * 60 / poll_seconds)
    for _ in range(max_iterations):
        with SessionLocal() as db:
            job = db.get(AIJob, job_id)
            if job is None:
                yield {"event": "error", "data": json.dumps({"error": "ai_job not found"})}
                return
            payload = _job_event_payload(job)
            current_status = job.status
        if current_status != last_status:
            yield {"event": "status", "data": json.dumps(payload)}
            last_status = current_status
        if current_status in ("done", "error"):
            return
        await asyncio.sleep(poll_seconds)
    # Timed out without resolution — emit a final event so the client unblocks.
    yield {"event": "timeout", "data": json.dumps({"id": str(job_id), "status": last_status})}


@router.get("/{job_id}/stream")
async def stream_ai_job(
    job_id: UUID,
    user: CurrentUser = Depends(get_current_user),
) -> EventSourceResponse:
    # Checked once, up front, in a short-lived session; the stream itself
    # opens its own sessions per poll.
    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ai_job not found")
        assert_project_access(db, user, job.project_id)
    return EventSourceResponse(_stream_job_events(job_id))
