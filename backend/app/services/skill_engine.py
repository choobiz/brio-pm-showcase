"""S1 of the auto-improving skill engine.

Two public surfaces:

1. `get_active_skill_version(db, skill_id, namespace) -> SkillVersion`
   Resolves the active version for a skill. Cheap lookup; falls back
   to NULL-safe behaviour if no row exists (returns None — caller logs
   the run with skill_version_id=NULL and we don't lose the data).

2. `log_skill_run(db, *, skill_id, ai_job, project_type, input, raw_output,
                   latency_ms, cost_usd) -> SkillRun | None`
   Creates a SkillRun for a finished AI worker invocation. Idempotent
   on `ai_job_id` — re-running the worker over the same job (e.g.
   after a worker restart) won't produce duplicate telemetry.

Plus the diff capture pipeline:

3. `record_corrections(db, skill_run_id, accepted_output, *, semantic_category=None, user_note=None)`
   Computes the JSON diff between raw_output (already on the run) and
   `accepted_output`, writes one correction_signals row per discrete
   change, and updates the run's acceptance_status.

JSON diff is shallow-path-aware: a string value change at
`categories[5].items[12].name` is one signal, not three (no value
nesting beyond what's modeled). For container changes (added /
removed list elements, added / removed object keys) we emit one
signal per top-level element added or removed.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_job import AIJob
from app.models.skill import CorrectionSignal, SkillRun, SkillVersion


# Tag taxonomy mirrored on the frontend save-time popup. Only the
# ai_miss_* tags drive candidate generation (feeds_learning = True).
_LEARNING_CATEGORIES = {
    "ai_miss_missed_item",
    "ai_miss_wrong_qty_unit_pricing",
    "ai_miss_wrong_category",
    "ai_miss_doesnt_apply",
}
KNOWN_CATEGORIES = _LEARNING_CATEGORIES | {
    "project_owner_preference",
    "project_site_condition",
    "project_code_permit_nuance",
    "other",
}


def get_active_skill_version(
    db: Session, skill_id: str, owner_namespace: str = "brio-pm"
) -> SkillVersion | None:
    """Look up the active row for a skill in a tenant namespace."""
    return db.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill_id)
        .where(SkillVersion.owner_namespace == owner_namespace)
        .where(SkillVersion.status == "active")
    ).scalar_one_or_none()


# ── Prompt versioning (git-anchored, Step 1) ─────────────────────────
# The active skill_versions row mirrors the prompt that lives in code.
# A short content hash is the version fingerprint used to detect drift
# between code and the registry. We never auto-promote a *changed* prompt —
# sync only backfills the placeholder seed with the real (already-running)
# prompt; genuine changes go through the approval gate (Step 3).

_PLACEHOLDER_PREFIX = "# v1.0 reference"


def prompt_fingerprint(template: str) -> str:
    """Stable short content hash of a prompt template."""
    return hashlib.sha256(template.encode("utf-8")).hexdigest()[:16]


def sync_active_prompt(
    db: Session, skill_id: str, owner_namespace: str = "brio-pm"
) -> dict[str, Any]:
    """Mirror the code's current prompt into the skill's active registry
    row. SAFE by design:
      - in_sync       → row already matches code; no write
      - synced        → row held the placeholder seed (or empty); backfilled
                        with the real prompt (behaviour-preserving — this is
                        the prompt already running, just now recorded)
      - drift         → row holds a *real* prompt that differs from code;
                        NOT overwritten. Resolving drift is the approval
                        gate's job (Step 3), never this function's.
    Caller commits."""
    from app.services.ai import SKILL_PROMPT_VERSIONS, get_skill_prompt_template

    template = get_skill_prompt_template(skill_id)
    if template is None:
        return {"skill_id": skill_id, "status": "no_template"}
    row = get_active_skill_version(db, skill_id, owner_namespace)
    if row is None:
        return {"skill_id": skill_id, "status": "no_active_row"}

    code_fp = prompt_fingerprint(template)
    version = SKILL_PROMPT_VERSIONS.get(skill_id, row.version)
    stored = row.prompt_template or ""
    stored_fp = prompt_fingerprint(stored) if stored else None

    if stored_fp == code_fp and row.version == version:
        return {
            "skill_id": skill_id,
            "status": "in_sync",
            "version": version,
            "fingerprint": code_fp,
        }

    is_placeholder = (not stored) or stored.startswith(_PLACEHOLDER_PREFIX)
    if not is_placeholder:
        # Real prompt drift vs the approved active row. NEVER auto-promote —
        # register the code prompt as a draft candidate awaiting explicit
        # approval (idempotent on fingerprint). The active row keeps the
        # last-approved prompt until someone approves the candidate.
        existing = _find_draft_with_fingerprint(
            db, skill_id, owner_namespace, code_fp
        )
        if existing is not None:
            return {
                "skill_id": skill_id,
                "status": "candidate_pending",
                "version": version,
                "candidate_id": str(existing.id),
                "code_fingerprint": code_fp,
                "active_fingerprint": stored_fp,
            }
        candidate = SkillVersion(
            skill_id=skill_id,
            version=version,
            owner_namespace=owner_namespace,
            status="draft",
            prompt_template=template,
            parent_version_id=row.id,
            created_by="code-sync",
            description="Auto-registered from code drift; awaiting approval.",
        )
        db.add(candidate)
        db.flush()
        return {
            "skill_id": skill_id,
            "status": "candidate_registered",
            "version": version,
            "candidate_id": str(candidate.id),
            "code_fingerprint": code_fp,
            "active_fingerprint": stored_fp,
        }

    row.prompt_template = template
    row.version = version
    db.flush()
    return {
        "skill_id": skill_id,
        "status": "synced",
        "version": version,
        "fingerprint": code_fp,
    }


def _find_draft_with_fingerprint(
    db: Session, skill_id: str, owner_namespace: str, fingerprint: str
) -> SkillVersion | None:
    """Existing draft candidate for this skill whose prompt matches the
    given fingerprint — so sync doesn't pile up duplicate candidates."""
    drafts = db.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill_id)
        .where(SkillVersion.owner_namespace == owner_namespace)
        .where(SkillVersion.status == "draft")
    ).scalars().all()
    for d in drafts:
        if prompt_fingerprint(d.prompt_template or "") == fingerprint:
            return d
    return None


def approve_version(
    db: Session,
    version_id: UUID,
    *,
    approver: str,
    note: str | None = None,
) -> SkillVersion:
    """Promote a draft candidate to active: retire the current active row,
    activate the candidate, and stamp the approval. The single explicit
    human gate — nothing else flips a version to active. Caller commits."""
    candidate = db.get(SkillVersion, version_id)
    if candidate is None:
        raise ValueError(f"skill_version {version_id} not found")
    if candidate.status != "draft":
        raise ValueError(
            f"version {version_id} is '{candidate.status}', not an approvable draft"
        )
    current = get_active_skill_version(
        db, candidate.skill_id, candidate.owner_namespace
    )
    if current is not None and current.id != candidate.id:
        current.status = "retired"
        # Release the one-active-row slot (partial unique index) BEFORE we
        # activate the candidate, or the flush order can collide.
        db.flush()
    candidate.status = "active"
    candidate.approved_by = approver
    candidate.approved_at = datetime.now(timezone.utc)
    candidate.approval_note = note
    db.flush()
    return candidate


def reject_version(
    db: Session, version_id: UUID, *, approver: str, note: str | None = None
) -> SkillVersion:
    """Reject a draft candidate (never becomes active). Caller commits."""
    candidate = db.get(SkillVersion, version_id)
    if candidate is None:
        raise ValueError(f"skill_version {version_id} not found")
    if candidate.status != "draft":
        raise ValueError(
            f"version {version_id} is '{candidate.status}', not a rejectable draft"
        )
    candidate.status = "rejected"
    candidate.approved_by = approver
    candidate.approved_at = datetime.now(timezone.utc)
    candidate.approval_note = note
    db.flush()
    return candidate


def log_skill_run(
    db: Session,
    *,
    skill_id: str,
    ai_job: AIJob,
    project_type: str | None,
    input_payload: dict[str, Any],
    raw_output: dict[str, Any] | None,
    latency_ms: int | None = None,
    cost_usd: Decimal | None = None,
    owner_namespace: str = "brio-pm",
) -> SkillRun | None:
    """Create the telemetry row for a finished AI worker call. Idempotent
    on `ai_job.id`. Returns the SkillRun or None if no skill is registered
    (we still don't lose data — the worker keeps the raw output on the
    AIJob row even if there's no SkillRun to attach)."""

    version = get_active_skill_version(db, skill_id, owner_namespace)
    if version is None:
        return None

    existing = db.execute(
        select(SkillRun).where(SkillRun.ai_job_id == ai_job.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = SkillRun(
        skill_version_id=version.id,
        ai_job_id=ai_job.id,
        project_type=project_type,
        input=input_payload,
        raw_output=raw_output,
        acceptance_status="pending",
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )
    db.add(row)
    db.flush()
    return row


# ── Diff capture ────────────────────────────────────────────────────


def _is_primitive(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool)) or v is None


def _diff_paths(
    before: Any,
    after: Any,
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    """Recursive JSON diff returning a list of `{path, diff_type,
    before_value, after_value}` dicts. Container changes (list element
    added / removed, object key added / removed) become one signal per
    top-level element. Primitive value changes become one signal per
    leaf path. Recursion stops at containers of containers to keep
    the signal count manageable."""

    signals: list[dict[str, Any]] = []

    # Dict vs dict — diff key-by-key
    if isinstance(before, dict) and isinstance(after, dict):
        keys = set(before.keys()) | set(after.keys())
        for k in sorted(keys):
            sub_prefix = f"{prefix}/{k}" if prefix else k
            if k not in before:
                signals.append(
                    {
                        "path": sub_prefix,
                        "diff_type": "added",
                        "before_value": None,
                        "after_value": after[k],
                    }
                )
            elif k not in after:
                signals.append(
                    {
                        "path": sub_prefix,
                        "diff_type": "removed",
                        "before_value": before[k],
                        "after_value": None,
                    }
                )
            else:
                signals.extend(_diff_paths(before[k], after[k], prefix=sub_prefix))
        return signals

    # List vs list — index-by-index. For unequal length, the extra tail
    # elements become added / removed signals.
    if isinstance(before, list) and isinstance(after, list):
        n = max(len(before), len(after))
        for i in range(n):
            sub_prefix = f"{prefix}[{i}]"
            if i >= len(before):
                signals.append(
                    {
                        "path": sub_prefix,
                        "diff_type": "added",
                        "before_value": None,
                        "after_value": after[i],
                    }
                )
            elif i >= len(after):
                signals.append(
                    {
                        "path": sub_prefix,
                        "diff_type": "removed",
                        "before_value": before[i],
                        "after_value": None,
                    }
                )
            else:
                signals.extend(_diff_paths(before[i], after[i], prefix=sub_prefix))
        return signals

    # Primitive vs primitive — modified if not equal
    if _is_primitive(before) and _is_primitive(after):
        if before != after:
            signals.append(
                {
                    "path": prefix or "/",
                    "diff_type": "modified",
                    "before_value": before,
                    "after_value": after,
                }
            )
        return signals

    # Type mismatch — emit a modified signal at this level
    if before != after:
        signals.append(
            {
                "path": prefix or "/",
                "diff_type": "modified",
                "before_value": before,
                "after_value": after,
            }
        )
    return signals


def record_corrections(
    db: Session,
    skill_run_id: UUID,
    accepted_output: dict[str, Any],
    *,
    semantic_category: str | None = None,
    user_note: str | None = None,
) -> tuple[SkillRun, int]:
    """Compute the diff between the run's raw_output and the provided
    accepted_output, write one CorrectionSignal per discrete change,
    update the run's acceptance_status + accepted_output + edit_count.

    Returns (run, signal_count). `semantic_category` (if provided) is
    written onto every signal generated by this call and feeds_learning
    is auto-derived from it."""

    run = db.get(SkillRun, skill_run_id)
    if run is None:
        raise ValueError(f"skill_run {skill_run_id} not found")

    raw = run.raw_output or {}
    signals = _diff_paths(raw, accepted_output)
    feeds_learning = (
        semantic_category in _LEARNING_CATEGORIES if semantic_category else False
    )

    for s in signals:
        db.add(
            CorrectionSignal(
                skill_run_id=run.id,
                path=s["path"],
                diff_type=s["diff_type"],
                before_value=s["before_value"],
                after_value=s["after_value"],
                semantic_category=semantic_category,
                user_note=user_note,
                feeds_learning=feeds_learning,
            )
        )

    run.accepted_output = accepted_output
    run.edit_count = len(signals)
    run.acceptance_status = "accepted" if len(signals) == 0 else "edited"
    run.accepted_at = datetime.now(timezone.utc)

    db.flush()
    return run, len(signals)


def find_run_for_artifact(
    db: Session,
    *,
    skill_id: str,
    artifact_marker: str,
    artifact_value: str,
) -> SkillRun | None:
    """Find the most recent skill_run whose ai_job.output references the
    given artifact (e.g. `scope_id`, `estimate_version_id`, or
    `document_summary_id`). Used by user-edit endpoints to attach
    correction signals to the originating AI run."""

    # We do a small JSON match on AIJob.output rather than a big join —
    # the volume is low and JSONB GIN handles this fine.
    rows = db.execute(
        select(SkillRun, AIJob)
        .join(AIJob, AIJob.id == SkillRun.ai_job_id)
        .where(SkillRun.skill_version_id.in_(
            select(SkillVersion.id).where(SkillVersion.skill_id == skill_id)
        ))
        .where(AIJob.output[artifact_marker].astext == artifact_value)
        .order_by(SkillRun.created_at.desc())
        .limit(1)
    ).first()
    if rows is None:
        return None
    return rows[0]
