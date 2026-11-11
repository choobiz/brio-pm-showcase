"""AI job worker — drains the `ai_jobs` queue.

Run with:  python -m app.workers.ai_worker

Polling loop:
  1. SELECT one row WHERE status='queued' FOR UPDATE SKIP LOCKED
  2. Mark status='running', started_at=now(), attempts+=1, model=<routed>
  3. Build prompt, call Anthropic with retry (3 attempts on 5xx/429)
  4. Persist output JSON + token counts + cost; mark status='done'
  5. On non-retryable error (validation/auth) → status='error' immediately
  6. On retryable error after 3 tries → status='error' with error string

Locking: every iteration opens a fresh short Session, runs the claim inside a
transaction with `with_for_update(skip_locked=True)`, then commits the
state-transition immediately. The actual Anthropic call happens AFTER the
status flip is committed so other workers don't block on a long HTTP call.

Graceful shutdown: SIGTERM/SIGINT sets a stop flag; the loop finishes the
current job (if any), then exits cleanly. Useful for systemd / docker stop.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import anthropic
from sqlalchemy import func, select
from sqlalchemy import text as _sql_text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, engine  # noqa: F401  (engine kept for parity / tests)
from app.models.ai_job import AIJob
from app.models.document import Document
from app.models.estimate import Estimate
from app.models.estimate_version import EstimateVersion  # noqa: F401 — registers the estimate_versions table so Scope.based_on_estimate_id FK resolves in the worker's SQLAlchemy metadata
from app.models.project import Project
from app.models.scope import Scope
from app.services.ai import (
    AnthropicResult,
    build_messages_for_job,
    call_anthropic,
    model_for_kind,
)
from app.services import job_progress as _progress
from app.services.divisions import division_context
from app.services.rate_reference import rate_library_context
from app.services.estimate_scope import NEW_VERSION_SCOPE_SQL, scope_binds


logger = logging.getLogger("brio.ai_worker")

POLL_INTERVAL_SECONDS = float(os.environ.get("AI_WORKER_POLL_SECONDS", "5"))
MAX_ATTEMPTS = int(os.environ.get("AI_WORKER_MAX_ATTEMPTS", "3"))
RETRY_BACKOFF_SECONDS = float(os.environ.get("AI_WORKER_RETRY_BACKOFF", "2"))

# ── Bounded self-correction ────────────────────────────────────────────────
# `_call_with_retry` above retries TRANSPORT failures. This retries SEMANTIC
# ones: output that arrived fine and is unusable. Today those are terminal —
# the job is marked error and the work is discarded, which for estimate_full
# averages 452 seconds of model time.
#
# Dark by default. Set AI_SELF_CORRECT_KINDS to a comma-separated list of job
# kinds to enable, so this lands one kind at a time and reverts by unsetting
# an environment variable rather than by a deploy.
#: Kinds repaired regardless of the env switch. submittal_review: its first
#: live run (2026-10-17) hit the output cap mid-question and was thrown away.
ALWAYS_SELF_CORRECT_KINDS = frozenset({"submittal_review"})
SELF_CORRECT_KINDS: set[str] = {
    k.strip() for k in os.environ.get("AI_SELF_CORRECT_KINDS", "").split(",") if k.strip()
}
#: Repair attempts AFTER the first call, per job. 2 means at most 3 calls.
MAX_REPAIR_ATTEMPTS = int(os.environ.get("AI_MAX_REPAIR_ATTEMPTS", "2"))
#: Ceiling on escalated output budget, so a repair loop cannot bill without bound.
MAX_ESCALATED_TOKENS = int(os.environ.get("AI_MAX_ESCALATED_TOKENS", "16000"))
#: Kinds whose first budget is already above that ceiling, so escalation needs
#: its own (submittal_review starts at 24k; the approved amount still caps cost).
KIND_ESCALATED_TOKENS = {"submittal_review": 48000}


# ---------------------------------------------------------------------------
# Graceful-shutdown plumbing
# ---------------------------------------------------------------------------

_stop_requested = False


def _request_stop(signum, frame) -> None:  # pragma: no cover - signal handler
    global _stop_requested
    _stop_requested = True
    logger.info("ai_worker: shutdown signal received (signum=%s)", signum)


def install_signal_handlers() -> None:  # pragma: no cover - signal handler
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)


def stop_requested() -> bool:
    return _stop_requested


# ---------------------------------------------------------------------------
# Retry classification
# ---------------------------------------------------------------------------


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient errors worth retrying.

    Retryable: 429, 5xx, network timeouts, anthropic.APIConnectionError.
    Non-retryable: 400/401/403/404 (validation, auth, missing model).
    """
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    if isinstance(exc, anthropic.APITimeoutError):
        return True
    if isinstance(exc, anthropic.InternalServerError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        status_code = getattr(exc, "status_code", None)
        if status_code is not None and 500 <= int(status_code) < 600:
            return True
        if status_code == 429:
            return True
        return False
    # Unknown exception — treat as non-retryable so we surface the error fast.
    return False


# ---------------------------------------------------------------------------
# Job claim + run
# ---------------------------------------------------------------------------


# A job left `running` with nobody working on it is invisible: the UI spins for
# ever, no error is recorded, and the user concludes the model is slow. It
# happens whenever the worker is restarted mid-job, which a deploy does every
# time — estimate_full averages 452s, far longer than any sane stop grace, so
# draining is not always possible and recovery has to be.
#
# There is one worker, so anything still marked `running` when this process
# starts belongs to a worker that no longer exists. The runtime ceiling catches
# the other case: a job whose worker is alive but wedged.
JOB_TIMEOUT_SECONDS = int(os.environ.get("AI_WORKER_JOB_TIMEOUT", str(45 * 60)))


def reap_orphaned_jobs(db: Session, *, startup: bool) -> int:
    """Requeue jobs abandoned by a dead worker, or give up on them loudly.

    `startup=True` reaps every running job, because this process starting means
    no other worker holds one. Otherwise only jobs past the runtime ceiling are
    touched, so a legitimately slow job is left alone.
    """
    stmt = select(AIJob).where(AIJob.status == "running")
    if not startup:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=JOB_TIMEOUT_SECONDS)
        stmt = stmt.where(AIJob.started_at < cutoff)

    reaped = 0
    for job in db.execute(stmt).scalars().all():
        ran_for = (
            int((datetime.now(timezone.utc) - job.started_at).total_seconds())
            if job.started_at
            else -1
        )
        if (job.attempts or 0) >= MAX_ATTEMPTS:
            # Stop retrying. A job that has died three times is not going to
            # work on the fourth, and an honest error beats an eternal spinner.
            job.status = "error"
            job.error = (
                f"abandoned after {job.attempts} attempts; last ran {ran_for}s "
                f"before its worker went away"
            )
            job.finished_at = datetime.now(timezone.utc)
            _fail_submittal(db, job.id, "The review stopped and was given up after its worker died. Run it again.")
            logger.warning("ai_worker: giving up on job %s (%s)", job.id, job.kind)
        else:
            job.status = "queued"
            job.started_at = None
            logger.warning(
                "ai_worker: requeued orphaned job %s (%s), had run %ss, attempt %s",
                job.id, job.kind, ran_for, job.attempts,
            )
        reaped += 1
    if reaped:
        db.commit()
    return reaped


def _fail_submittal(db: Session, job_id: UUID, message: str) -> None:
    """A submittal whose review job failed shows it, with Run again, instead of
    "Reviewing…" forever (audit 2026-10-17: only an unparseable reply did this)."""
    from app.models.submittal import Submittal

    s = db.query(Submittal).filter(Submittal.ai_job_id == job_id).first()
    if s is not None and s.status == "reviewing":
        s.status, s.error = "review_failed", message


def claim_one_job(db: Session) -> AIJob | None:
    """Atomically claim the oldest queued job.

    Uses `FOR UPDATE SKIP LOCKED` so a concurrent worker grabs the next row
    rather than blocking. Commits the status flip immediately so the row is
    no longer marked `queued` when the (possibly slow) Anthropic call begins.
    """
    stmt = (
        select(AIJob)
        .where(AIJob.status == "queued")
        .order_by(AIJob.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = db.execute(stmt).scalar_one_or_none()
    if job is None:
        return None

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.attempts = (job.attempts or 0) + 1
    job.model = model_for_kind(job.kind)
    db.commit()
    db.refresh(job)
    return job


def _call_with_retry(
    *,
    model: str,
    system: str | None,
    messages: list[dict[str, Any]],
    max_tokens: int = 2048,
    client: anthropic.Anthropic | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    backoff_seconds: float = RETRY_BACKOFF_SECONDS,
    sleep=time.sleep,
) -> AnthropicResult:
    """Call Anthropic with up-to-3 attempts on retryable errors.

    Raises the last exception if all attempts fail or if a non-retryable
    error occurs.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call_anthropic(
                model=model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                client=client,
            )
        except BaseException as exc:  # noqa: BLE001 - we re-raise below
            last_exc = exc
            if not _is_retryable(exc) or attempt >= max_attempts:
                raise
            logger.warning(
                "ai_worker: retryable error on attempt %d/%d: %r",
                attempt,
                max_attempts,
                exc,
            )
            sleep(backoff_seconds * attempt)
    # Unreachable — loop either returns or raises.
    assert last_exc is not None
    raise last_exc


def _merge_project_metadata(db: Session, project_id: UUID, input_payload: dict[str, Any]) -> dict[str, Any]:
    """Pull stored Project fields and merge them under `project_metadata` so the
    scope prompt builder has consistent project context regardless of what the
    frontend sent."""
    merged = dict(input_payload or {})
    project = db.get(Project, project_id)
    if project is None:
        return merged
    incoming = dict(merged.get("project_metadata") or {})
    # Frontend-supplied keys win — they may correct stale DB fields.
    db_meta = {
        "name": project.name,
        "address": project.address,
        "project_type": project.type,
        "building_age": project.building_age,
        "building_type": project.building_type,
        "sqft": project.sqft,
    }
    for k, v in db_meta.items():
        if v is None:
            continue
        incoming.setdefault(k, v)
    merged["project_metadata"] = incoming

    # Which cost breakdown this project's estimates use. Resolved here because
    # this is the single funnel that has both the db session and the project;
    # ai.py stays free of database access.
    #
    # Only set when the project type maps to a set. A null/unmapped type leaves
    # the key absent, and the prompt builder falls back to the generic list AND
    # says so in the payload, so an estimate built on the wrong breakdown is
    # visible rather than silent.
    if merged.get("divisions") is None:
        ctx = division_context(db, incoming.get("project_type"))
        if ctx is not None:
            merged["divisions"] = ctx
        else:
            merged["divisions_fallback_reason"] = (
                f"project_type={incoming.get('project_type')!r} maps to no division set; "
                "using the generic category list. Classify the project to fix this."
            )

    # The finish, as confirmed on the Estimate page (services/finish_spec): a
    # shell's exclusions, or each confirmed finish and which are still open.
    if merged.get("finish_spec") is None:
        from app.services.finish_spec import spec_for_estimate

        spec = spec_for_estimate(db, project_id)
        if spec:
            merged["finish_spec"] = spec

    # The pricing baseline, read live from the rate library so that editing a rate
    # in the admin UI changes what the model actually prices against. Resolved
    # here for the same reason as divisions: this is the funnel with a db session.
    if merged.get("rates") is None:
        # 'residential' / 'commercial' — the rate rows are scoped that coarsely,
        # not by new-vs-reno, and an unclassified project gets the 'any' rows.
        ptype = (incoming.get("project_type") or "")
        scope = ("residential" if ptype.startswith("residential")
                 else "commercial" if ptype.startswith("commercial") else "any")
        rates = rate_library_context(db, project_type=scope)
        if rates is not None:
            merged["rates"] = rates
        else:
            merged["rates_fallback_reason"] = (
                "the rate library is too thin to price from (needs 5+ labour trades "
                "and 10+ materials); using the static baseline."
            )
    return merged


def _merge_scope_inputs(
    db: Session, project_id: UUID, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """For scope_analysis: merge project metadata + materialise `document_ids`
    into ready-to-send content blocks. PDFs (drawings) -> `documents`; images
    (photos) -> `images`. Each Document is fetched from R2 once."""
    from app.services.r2 import get_bytes

    import os

    from app.services import doc_text

    merged = _merge_project_metadata(db, project_id, input_payload)

    images = list(merged.get("images") or [])
    documents = list(merged.get("documents") or [])
    texts: list[dict[str, Any]] = list(merged.get("text_documents") or [])
    doc_ids = merged.get("document_ids") or []

    for raw_id in doc_ids:
        try:
            doc_uuid = UUID(str(raw_id))
        except (ValueError, TypeError):
            continue
        doc = db.get(Document, doc_uuid)
        if doc is None or doc.archived or doc.project_id != project_id:
            continue
        try:
            data = get_bytes(doc.r2_key)
        except Exception:  # noqa: BLE001 — skip the doc, the run continues
            logger.exception("ai_worker: scope_analysis failed to fetch %s", doc.r2_key)
            continue
        mime = (doc.mime or "").lower()
        ext = os.path.splitext(doc.filename or "")[1].lower()

        if mime == "application/pdf" or ext == ".pdf":
            documents.append({
                "data": base64.standard_b64encode(data).decode(),
                "caption": doc.filename, "media_type": "application/pdf",
            })
        elif mime.startswith("image/"):
            images.append({
                "data": base64.standard_b64encode(data).decode(),
                "caption": doc.filename, "media_type": mime,
            })
        elif ext in doc_text.EXTRACTABLE_EXTS:
            # Word and Excel used to land here and be dropped without a word.
            # A ticked specification contributed nothing, and the scope came
            # back just as confident either way.
            result = doc_text.extract(doc.filename or "", data)
            texts.append({
                "caption": doc.filename,
                "text": result.text,
                "truncated": result.truncated,
                "error": result.error,
            })
        else:
            # Still not readable — CAD, video, legacy binary office formats.
            # Recorded rather than skipped, so the prompt can say what was left
            # out instead of the omission being invisible.
            texts.append({
                "caption": doc.filename, "text": "",
                "error": f"{ext or 'this file type'} cannot be read for a scope",
            })

    merged["images"] = images
    merged["documents"] = documents
    merged["text_documents"] = doc_text.budget(texts)
    return merged


def _merge_submittal_inputs(db: Session, project_id: UUID, input_payload: dict[str, Any]) -> dict[str, Any]:
    """The submittal's files and the governing files, kept apart so the prompt
    can label each side. Both go through the scope materialiser, which carries
    the project-isolation check and the docx/xlsx text extraction."""
    merged = dict(input_payload)
    for side in ("submittal", "governing"):
        m = _merge_scope_inputs(db, project_id, {"document_ids": input_payload.get(f"{side}_doc_ids") or []})
        merged[f"{side}_documents"] = [*m.get("documents", []), *m.get("images", [])]
        merged[f"{side}_texts"] = m.get("text_documents") or []
    return merged


def _persist_submittal_review(db: Session, job: AIJob, result: AnthropicResult) -> str | None:
    """Findings onto the submittal the endpoint created. An unusable reply
    leaves it 'review_failed' with the reason, so the PM can run it again."""
    from app.models.submittal import Submittal
    from app.services import submittal_checklists as checklists
    from app.services import submittal_review as review

    s = db.query(Submittal).filter(Submittal.ai_job_id == job.id).first()
    if s is None:
        return "the submittal for this review is gone"
    if getattr(result, "stop_reason", None) == "max_tokens":
        s.status, s.cost_usd = "review_failed", result.cost_usd
        s.error = "The reply was cut off before it finished (it ran out of room). Run it again."
        return "the review reply was cut off at the output limit"
    try:
        snap = (job.input or {}).get("checklist")
        cl = checklists.from_dict(snap) if snap else checklists.get(s.trade, db)
        parsed = review.parse_review(result.text, cl, has_previous=s.parent_id is not None)
    except Exception as exc:  # noqa: BLE001
        s.status, s.error, s.cost_usd = "review_failed", f"The review didn't come back usable: {exc}", result.cost_usd
        return f"Could not read the review: {exc}"
    s.findings, s.quote, s.summary = parsed["findings"], parsed["quote"], parsed["summary"]
    s.cost_usd, s.error, s.reviewed_at = result.cost_usd, None, datetime.now(timezone.utc)
    s.status = "needs_call"
    c = review.counts(parsed["findings"])
    review.note(s, "assistant", f"Reviewed: {c['critical']} critical, {c['confirm']} confirm, "
                                f"{c['not_shown']} not shown, {c['match']} match")
    return None


#: What an ai_edit job can point at: the table, the column holding the editable
#: state, and a human label for the prompt. Deliberately NOT every table with a
#: payload — invoices and estimate_versions are money documents with their own
#: send/approve lifecycles, and they get added only once 2D's guardrails cover
#: them.
EDIT_TARGETS: dict[str, tuple[str, str | None, str]] = {
    "scope": ("scopes", "payload", "scope of work"),
    "estimate": ("estimates", "payload", "estimate"),
    "estimate_version": ("estimate_versions", "payload", "estimate version"),
    "change_order": ("change_orders", None, "change order"),
}


def _merge_ai_edit_inputs(
    db: Session, project_id: UUID, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """Load the record an edit instruction refers to, as it stands right now.

    Read at RUN time, not at queue time. A job can sit in the queue while
    somebody edits the record by hand, and planning against a stale copy is
    how an edit overwrites a change nobody has seen. The `before` check in
    edit_plan.verify is the second line of defence against the same thing.
    """
    from sqlalchemy import text as _text

    merged = _merge_project_metadata(db, project_id, input_payload)
    target = str(merged.get("record_type") or "")
    record_id = merged.get("record_id")
    spec = EDIT_TARGETS.get(target)
    if spec is None or not record_id:
        merged["document"] = {}
        merged["load_error"] = f"cannot edit a {target or 'record'} of that kind"
        return merged

    table, column, label = spec
    if column is not None:
        row = db.execute(
            _text(f"SELECT {column} AS doc, project_id FROM {table} WHERE id = :i"),
            {"i": str(record_id)},
        ).mappings().first()
        document = dict(row["doc"] or {}) if row else None
    else:
        from app.services.revisions import _current_state

        document = _current_state(db, table, record_id)
        row = db.execute(
            _text(f"SELECT project_id FROM {table} WHERE id = :i"), {"i": str(record_id)}
        ).mappings().first()

    if row is None or document is None:
        merged["document"] = {}
        merged["load_error"] = "that record no longer exists"
        return merged
    if str(row["project_id"]) != str(project_id):
        # Belt and braces. The API already checks project access, but a job
        # payload is data and an edit that crosses projects must never run.
        merged["document"] = {}
        merged["load_error"] = "that record belongs to a different project"
        return merged

    merged["document"] = document
    merged["record_label"] = merged.get("record_label") or label
    return merged


def _persist_scope_from_result(
    db: Session, job: AIJob, result: AnthropicResult
) -> tuple[Scope | None, str | None]:
    """Parse the model's JSON output and insert a Scope row.

    Returns `(scope, None)` on success, or `(None, error_message)` if the
    response could not be parsed.
    """
    parsed, err = _parse_json_object(result)
    if err is not None:
        return None, err
    assert parsed is not None

    # Post-hoc stamp the discipline tag from the job input so scopes are
    # findable per trade without relying on the model echoing it back.
    discipline = ((job.input or {}).get("discipline") or "").strip().lower()
    if discipline:
        parsed["discipline"] = discipline

    # The scope's date is the day it was written, set here: the prompt never
    # tells the model today's date, and it invented 2025-01-16 for a scope
    # written on 2026-10-14 (rollout test).
    from app.services.home import today

    parsed["date"] = today().isoformat()

    # Compute next version atomically per project.
    next_version = db.execute(
        select(func.coalesce(func.max(Scope.version), 0)).where(
            Scope.project_id == job.project_id
        )
    ).scalar_one() + 1

    scope = Scope(
        project_id=job.project_id,
        version=next_version,
        payload=parsed,
        ai_job_id=job.id,
    )
    db.add(scope)
    db.flush()
    return scope, None


def _parse_json_object(result: AnthropicResult) -> tuple[dict | None, str | None]:
    """Strip accidental markdown fences and parse the model output as a JSON
    object. Returns `(obj, None)` or `(None, error_message)`."""
    text = (result.text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        if getattr(result, "stop_reason", None) == "max_tokens":
            return None, (
                f"Model output was truncated at the max_tokens limit "
                f"({result.output_tokens} output tokens) — the JSON is "
                f"incomplete. Raise the max_tokens cap for this job kind. ({exc})"
            )
        snippet = (result.text or "")[:240]
        return None, f"JSON parse failure: {exc}; first-240-chars={snippet!r}"
    if not isinstance(parsed, dict):
        return None, f"JSON parse failure: expected object, got {type(parsed).__name__}"
    return parsed, None


def _repair_directive(err: str, result: AnthropicResult, kind: str | None = None) -> tuple[str | None, int | None]:
    """Decide how to repair, from the failure itself.

    Returns `(prompt_instruction, escalated_max_tokens)`; exactly one is set.

    ⚠️ Truncation and malformed JSON need OPPOSITE repairs, which is the thing
    measurement showed and reading the code did not. Telling a truncated model
    to "be briefer" throws away the content it ran out of room to write; the
    only honest repair is more room. Three of the four job failures in this
    table were truncation, every one of them at exactly the kind's cap.
    """
    if result.stop_reason == "max_tokens":
        return None, min(result.output_tokens * 2, KIND_ESCALATED_TOKENS.get(kind or "", MAX_ESCALATED_TOKENS))
    return (
        "Your previous reply could not be parsed as a single JSON object. "
        f"The parser reported: {err}\n"
        "Reply with one JSON object and nothing else — no prose, no code fences.",
        None,
    )


def _call_self_correcting(
    *, kind: str, model: str, system: str | None, messages: list[dict[str, Any]],
    max_tokens: int, client: anthropic.Anthropic | None = None,
    approved_usd: float | None = None,
) -> AnthropicResult:
    """`_call_with_retry`, plus a bounded loop on output that will not parse.

    Returns a result the caller parses exactly as before. On exhaustion the LAST
    result is returned, so the existing per-kind error handling still reports
    the real failure and nothing new can swallow an error. Its tokens and cost
    are the SUM of every attempt: until 2026-10-15 a repaired job recorded only
    its last call. No repair starts once the attempts so far have spent the
    amount the person approved.
    """
    from dataclasses import replace as _replace

    result = _call_with_retry(model=model, system=system, messages=messages,
                              max_tokens=max_tokens, client=client)
    if kind not in SELF_CORRECT_KINDS and kind not in ALWAYS_SELF_CORRECT_KINDS:
        return result
    spent = [result]

    def _total(last: AnthropicResult) -> AnthropicResult:
        return _replace(last, input_tokens=sum(r.input_tokens for r in spent),
                        output_tokens=sum(r.output_tokens for r in spent),
                        cost_usd=sum((r.cost_usd for r in spent), Decimal(0)))

    for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
        parsed, err = _parse_json_object(result)
        if err is None:
            if attempt > 1:
                logger.info("ai_worker: %s recovered on repair attempt %d", kind, attempt - 1)
            return _total(result)
        if approved_usd is not None and sum(r.cost_usd for r in spent) >= Decimal(str(approved_usd)):
            logger.warning("ai_worker: %s not repaired: the approved $%s is spent", kind, approved_usd)
            return _total(result)

        instruction, escalated = _repair_directive(err, result, kind)
        if escalated is not None and escalated <= max_tokens:
            logger.warning("ai_worker: %s truncated at %d tokens and the budget cannot be "
                           "raised further; giving up", kind, max_tokens)
            return _total(result)
        if escalated is not None:
            logger.info("ai_worker: %s truncated at %d tokens — retrying at %d (attempt %d/%d)",
                        kind, max_tokens, escalated, attempt, MAX_REPAIR_ATTEMPTS)
            max_tokens = escalated
        else:
            logger.info("ai_worker: %s returned unparseable output — repairing (attempt %d/%d)",
                        kind, attempt, MAX_REPAIR_ATTEMPTS)
            messages = [*messages,
                        {"role": "assistant", "content": result.text[:4000]},
                        {"role": "user", "content": instruction}]

        result = _call_with_retry(model=model, system=system, messages=messages,
                                  max_tokens=max_tokens, client=client)
        spent.append(result)

    _, err = _parse_json_object(result)
    if err is not None:
        logger.error("ai_worker: %s still unusable after %d repair attempts: %s",
                     kind, MAX_REPAIR_ATTEMPTS, err)
    return _total(result)


_ESTIMATE_DETAIL_LEVELS = ("summary", "standard", "detailed", "fully_itemized")


def _brio_cost_benchmarks(db: Session) -> list[dict[str, Any]]:
    """Per-category subtrade/supplier cost actuals — calibration data so the
    estimator anchors to BRIO's real numbers, not generic regional rates.

    Only categories with >= 2 records are returned (a single record is noise,
    not a benchmark). Amounts are base costs (labor + materials), matching the
    base-cost figures the estimator is asked to produce.
    """
    from app.models.project_cost import ProjectCost

    rows = db.execute(
        select(
            ProjectCost.category,
            func.sum(ProjectCost.amount),
            func.count(ProjectCost.id),
            func.avg(ProjectCost.amount),
        )
        .where(ProjectCost.category.is_not(None), ProjectCost.amount.is_not(None))
        # Bills entered on Money › Costs stay out: a category's average bill
        # (one lumber delivery, one rental) is not a rate, and feeding it in
        # would change the estimator's instructions without anyone deciding to.
        .where(func.coalesce(ProjectCost.payload["source"].astext, "") != "entered")
        .group_by(ProjectCost.category)
        .having(func.count(ProjectCost.id) >= 2)
        .order_by(func.sum(ProjectCost.amount).desc())
    ).all()
    return [
        {"category": c, "total": float(t), "count": n, "avg": float(a)}
        for c, t, n, a in rows
    ]


def _verified_quantities(
    db: Session, project_id: UUID, discipline: str | None
) -> list[dict[str, Any]]:
    """Human-verified quantities for this (project, discipline).

    Filtered on STATUS, not source. Before this filter existed the query returned
    every override row — including the `cad_extract` candidates the extractor
    writes with the note "confirm before estimate" — and handed them to the
    estimator described as ground truth. A machine's guess and a person's
    confirmation are not the same input to a price.
    """
    from app.models.quantity_override import PRICEABLE_STATUSES, QuantityOverride

    if not discipline:
        return []
    rows = (
        db.query(QuantityOverride)
        .filter(QuantityOverride.project_id == project_id,
                QuantityOverride.discipline == discipline,
                QuantityOverride.status.in_(PRICEABLE_STATUSES))
        .order_by(QuantityOverride.created_at)
        .all()
    )
    return [
        {
            "quantity_type": r.quantity_type,
            "value": float(r.value) if r.value is not None else None,
            "unit": r.unit,
            "source": r.source,
            "page": r.source_page,
            "notes": r.notes,
        }
        for r in rows
    ]


def _merge_estimate_inputs(
    db: Session, project_id: UUID, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """For estimate_generation: attach the source Scope payload + project
    metadata so the estimate prompt has the actual work to price, plus BRIO's
    historical cost benchmarks so the estimate is calibrated to real numbers,
    plus any user-verified quantities from the review UI (authoritative).

    Inherits `discipline` from the linked scope when not passed explicitly, and
    auto-detects commercial mode from `Project.type` so the prompt drops the
    residential rules of thumb for commercial TI work.
    """
    merged = _merge_project_metadata(db, project_id, input_payload)
    scope_id = merged.get("scope_id")
    if scope_id and not merged.get("scope"):
        try:
            scope = db.get(Scope, UUID(str(scope_id)))
        except (ValueError, TypeError):
            scope = None
        if scope is not None:
            merged["scope"] = scope.payload or {}
            if not merged.get("discipline"):
                sc_disc = (scope.payload or {}).get("discipline")
                if sc_disc:
                    merged["discipline"] = sc_disc
    if not merged.get("mode"):
        ptype = (merged.get("project_metadata") or {}).get("project_type") or ""
        if "commercial" in ptype.lower():
            merged["mode"] = "commercial"
    merged["brio_cost_benchmarks"] = _brio_cost_benchmarks(db)
    merged["verified_quantities"] = _verified_quantities(
        db, project_id, merged.get("discipline")
    )
    return merged


def _persist_estimate_from_result(
    db: Session, job: AIJob, result: AnthropicResult
) -> tuple[Estimate | None, str | None]:
    """Parse the model's JSON output and insert an Estimate row. The user's
    margin inputs (overhead/profit/allowances) are folded into the payload —
    the model only produces base costs."""
    parsed, err = _parse_json_object(result)
    if err is not None:
        return None, err
    assert parsed is not None

    next_version = db.execute(
        select(func.coalesce(func.max(Estimate.version), 0)).where(
            Estimate.project_id == job.project_id
        )
    ).scalar_one() + 1

    job_input = job.input or {}
    detail_level = job_input.get("detail_level") or parsed.get("detailLevel") or "standard"
    if detail_level not in _ESTIMATE_DETAIL_LEVELS:
        detail_level = "standard"

    # Enforce category.total = sum(items) for itemised estimates — the prompt
    # asks for it explicitly but the model occasionally pads the category total
    # above the line-item sum (observed: ~20% phantom on PROJB electrical run).
    if detail_level in ("detailed", "fully_itemized"):
        for cat in parsed.get("categories") or []:
            if not isinstance(cat, dict):
                continue
            items = cat.get("items") or []
            if not items:
                continue
            line_sum = 0.0
            for it in items:
                if not isinstance(it, dict):
                    continue
                line_sum += float(it.get("labor") or 0) + float(it.get("materials") or 0)
            cat["total"] = round(line_sum)

    # Fold the operator-supplied pricing inputs into the stored payload so the
    # PDF renderer + review UI can apply margin consistently.
    parsed["detailLevel"] = detail_level
    parsed["overhead"] = job_input.get("overhead", 12)
    parsed["profit"] = job_input.get("profit", 15)
    parsed["allowances"] = job_input.get("allowances", 0)

    ref = f"EST-{str(job.project_id).replace('-', '')[:8].upper()}-V{next_version}"
    estimate = Estimate(
        project_id=job.project_id,
        ref=ref,
        version=next_version,
        detail_level=detail_level,
        payload=parsed,
        status="draft",
        ai_job_id=job.id,
    )
    db.add(estimate)
    db.flush()
    return estimate, None


def _persist_estimate_version_from_result(
    db: Session, job: AIJob, result: AnthropicResult
) -> tuple[dict | None, str | None]:
    """Path C — parse the model's JSON output and insert it as a new
    estimate_versions row (workbench surface) instead of the legacy
    estimates table. Computes the per-category rollups + appends
    contingency lines the way the brio-admin build scripts do, so the
    workbench editor + diff viewer + regen-downstream all see a
    consistent shape.

    Returns `(row_summary_dict, None)` on success; `(None, err)` on
    parse failure.
    """
    parsed, err = _parse_json_object(result)
    if err is not None:
        return None, err
    assert parsed is not None

    from uuid import uuid4
    import json as _json
    from sqlalchemy import text as _text

    job_input = job.input or {}
    overhead = float(job_input.get("overhead", 12))
    profit = float(job_input.get("profit", 15))

    # Enforce category totals = sum(items) before rollup — same guard the
    # legacy path applies (the model sometimes pads category total).
    detail_level = job_input.get("detail_level") or parsed.get("detailLevel") or "standard"
    contingency_by_division = _contingency_for_project(db, job.project_id)
    if detail_level not in _ESTIMATE_DETAIL_LEVELS:
        detail_level = "standard"
    if detail_level in ("detailed", "fully_itemized"):
        for cat in parsed.get("categories") or []:
            if not isinstance(cat, dict):
                continue
            items = [
                it for it in (cat.get("items") or [])
                if isinstance(it, dict) and not it.get("contingency")
            ]
            line_sum = sum(
                float(it.get("labor") or 0) + float(it.get("materials") or 0)
                for it in items
            )
            cat["items"] = items
            cat["total"] = round(line_sum)

    if detail_level == "fully_itemized":
        from app.services.rate_links import link_categories

        links = link_categories([c for c in parsed.get("categories") or [] if isinstance(c, dict)],
                                *_rate_link_context(db, job.project_id))
        parsed.setdefault("provenance", {})["rate_links"] = links

    # Workbench rollup: subtotalRaw / contingencyAmount / total per category,
    # contingency line item appended at the bottom of each items[]. The shared
    # rule (estimate_totals) — this used to be a third inline copy.
    for cat in parsed.get("categories") or []:
        if isinstance(cat, dict):
            _rollup_category(cat, contingency_by_division)

    parsed["detailLevel"] = detail_level
    parsed["overhead"] = overhead
    parsed["profit"] = profit
    # A shell's excluded divisions are enforced here, not only asked of the model.
    from app.services.finish_spec import apply_for_project as _apply_finish

    _apply_finish(db, job.project_id, parsed)

    # Serialize numbering + activation per project (auto-releases at commit).
    db.execute(_text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": str(job.project_id)})
    # Next version number for this project on estimate_versions
    next_ver = db.execute(
        _text(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM estimate_versions "
            "WHERE project_id = :pid"
        ),
        {"pid": job.project_id},
    ).scalar_one()

    short = str(job.project_id).replace("-", "")[:8].upper()
    reference = f"EST-AI-{short}-V{next_ver:03d}"
    parsed["reference"] = parsed.get("reference") or reference

    # Parent = the project's current active version (chain for diff history)
    parent_id = db.execute(
        _text(
            "SELECT id FROM estimate_versions WHERE project_id = :pid AND is_active = true"
        ),
        {"pid": job.project_id},
    ).scalar_one_or_none()

    # New version is the active one — clear the prior active to satisfy the
    # partial unique index uq_estimate_versions_active_per_project.
    db.execute(
        _text(
            "UPDATE estimate_versions SET is_active = false "
            "WHERE project_id = :pid AND is_active = true"
        ),
        {"pid": job.project_id},
    )

    new_id = uuid4()
    db.execute(
        _text(
            """INSERT INTO estimate_versions (
                id, project_id, version_number, reference, label, cost_basis,
                overhead_pct, profit_pct, notes, payload,
                parent_version_id, is_active, created_by_id, ai_job_id, scope_id
            ) VALUES (
                :id, :pid, :ver, :ref, :label, 'LOW',
                :oh, :pr, :notes, CAST(:payload AS jsonb),
                :parent, true, NULL, :ai_job, """ + NEW_VERSION_SCOPE_SQL + """
            )"""
        ),
        {
            **scope_binds(job.project_id, parent_id, (job.input or {}).get("scope_id")),
            "id": new_id,
            "pid": job.project_id,
            "ver": int(next_ver),
            "ref": reference,
            "label": f"v{int(next_ver):03d} — {reference} (AI-drafted)",
            "oh": overhead,
            "pr": profit,
            "notes": f"Generated by AI worker (job {job.id}).",
            "payload": _json.dumps(parsed),
            "parent": parent_id,
            # Which run produced this. Without it an AI-drafted estimate is
            # indistinguishable from one a person wrote — and reading the
            # absence as "a person wrote it" would invent a fact.
            "ai_job": job.id,
        },
    )
    return {
        "id": str(new_id),
        "version_number": int(next_ver),
        "reference": reference,
        # Structured AI output, same schema the workbench editor edits and
        # re-POSTs. Used as the skill-run diff baseline so correction signals
        # are real field-level edits, not envelope-vs-payload noise.
        "parsed_payload": parsed,
    }, None


def run_job(job_id: UUID, *, client: anthropic.Anthropic | None = None) -> None:
    """Execute a previously-claimed job. Runs Anthropic OUTSIDE the claim txn."""
    with SessionLocal() as db:
        job = db.get(AIJob, job_id)
        if job is None:
            logger.error("ai_worker: job %s vanished before run", job_id)
            return
        kind = job.kind
        project_id = job.project_id
        input_payload = job.input or {}
        if kind == "cad_takeoff":
            _run_cad_takeoff(db, job, input_payload)
            return
        if kind == "drawing_analysis":
            _run_drawing_analysis(db, job, input_payload)
            return
        if kind == "estimate_full":
            _run_estimate_full(db, job, input_payload)
            return
        if kind == "presentation":
            _run_presentation(db, job, input_payload)
            return
        if kind == "scope_from_estimate":
            _run_scope_from_estimate(db, job, input_payload)
            return
        if kind == "schedule_from_estimate":
            _run_schedule_from_estimate(db, job, input_payload)
            return
        if kind == "scope_analysis":
            input_payload = _merge_scope_inputs(db, project_id, input_payload)
        elif kind == "estimate_generation":
            input_payload = _merge_estimate_inputs(db, project_id, input_payload)
        elif kind == "change_order_generation":
            # Same document materialisation as scope_analysis: this kind needs
            # PDFs and photos of the extra work, nothing estimate-specific.
            input_payload = _merge_scope_inputs(db, project_id, input_payload)
        elif kind == "daily_report_capture":
            # Metadata only — no documents. The input is the schedule agenda the
            # caller already holds plus a transcript; loading R2 files here would
            # be cost for nothing.
            input_payload = _merge_project_metadata(db, project_id, input_payload)
        elif kind == "document_synthesis":
            # Same materialiser again, and that is the point: it carries the
            # project-isolation check (`doc.project_id != project_id` is
            # skipped) and the docx/xlsx text extraction. A second loader would
            # be a second place for a document from another job to leak in.
            input_payload = _merge_scope_inputs(db, project_id, input_payload)
        elif kind == "ai_edit":
            input_payload = _merge_ai_edit_inputs(db, project_id, input_payload)
        elif kind == "submittal_review":
            try:
                input_payload = _merge_submittal_inputs(db, project_id, input_payload)
            except Exception as exc:  # noqa: BLE001
                logger.exception("ai_worker: job %s could not load its files", job_id)
                job.status, job.error = "error", f"{type(exc).__name__}: {exc}"
                job.finished_at = datetime.now(timezone.utc)
                _fail_submittal(db, job.id, f"The files couldn't be loaded: {exc}")
                db.commit()
                return
        model = job.model or model_for_kind(kind)

    system, messages, max_tokens = build_messages_for_job(kind, input_payload)

    try:
        result = _call_self_correcting(
            kind=kind,
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            client=client,
            approved_usd=input_payload.get("approve_usd"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("ai_worker: job %s failed", job_id)
        with SessionLocal() as db:
            j = db.get(AIJob, job_id)
            if j is None:
                return
            j.status = "error"
            j.error = f"{type(exc).__name__}: {exc}"
            j.finished_at = datetime.now(timezone.utc)
            _fail_submittal(db, j.id, f"The review didn't run: {type(exc).__name__}: {str(exc)[:300]}")
            db.commit()
        return

    with SessionLocal() as db:
        j = db.get(AIJob, job_id)
        if j is None:
            return

        base_output: dict[str, Any] = {
            "text": result.text,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cost_usd": str(result.cost_usd),
            "model": result.model,
        }
        j.input_tokens = result.input_tokens
        j.output_tokens = result.output_tokens
        j.cost_usd = result.cost_usd
        j.model = result.model
        j.finished_at = datetime.now(timezone.utc)

        # Diff baseline for the skill-engine correction loop. Must be the
        # AI's *structured* output (same schema the editor edits + re-POSTs),
        # NOT the j.output envelope (text/tokens/model/artifact-id). Falls
        # back to j.output for kinds with no structured editor surface.
        skill_raw_output: Any = None

        if j.kind == "scope_analysis":
            scope, err = _persist_scope_from_result(db, j, result)
            if err is not None:
                j.status = "error"
                j.error = err
                j.output = base_output
                db.commit()
                return
            assert scope is not None
            base_output["scope_id"] = str(scope.id)
            base_output["scope_version"] = scope.version
            skill_raw_output = scope.payload or {}

        elif j.kind == "estimate_generation":
            # Path C — land the AI output as a new estimate_versions row
            # (workbench surface). The legacy _persist_estimate_from_result
            # path is kept in this module for safety but no longer wired.
            ev_row, err = _persist_estimate_version_from_result(db, j, result)
            if err is not None:
                j.status = "error"
                j.error = err
                j.output = base_output
                db.commit()
                return
            assert ev_row is not None
            base_output["estimate_version_id"] = ev_row["id"]
            base_output["estimate_version_number"] = ev_row["version_number"]
            base_output["estimate_reference"] = ev_row["reference"]
            skill_raw_output = ev_row.get("parsed_payload") or {}

        elif j.kind == "email_draft":
            # Written to the draft row the endpoint created. Never sent from here:
            # sending is a separate, confirmed step (routers/milestones.py).
            from app.models.milestone import MilestoneEmail
            from app.services.milestone_email import parse_email_draft

            draft = db.query(MilestoneEmail).filter(MilestoneEmail.ai_job_id == j.id).first()
            try:
                parsed = parse_email_draft(result.text)
            except Exception as exc:  # noqa: BLE001
                if draft is not None:
                    draft.status, draft.error = "draft", f"The draft didn't come back usable: {exc}"
                j.status = "error"
                j.error = f"Could not read the draft: {exc}"
                j.output = base_output
                db.commit()
                return
            if draft is not None:
                draft.subject, draft.body, draft.status, draft.error = parsed["subject"], parsed["body"], "draft", None
                base_output["email_id"] = str(draft.id)
            if (j.input or {}).get("purpose") == "estimate_share":
                # Estimate-sharing email: kept on the job (the project's record of
                # what was drafted); the sender copies it and sends it himself.
                from app.services.estimate_email import check_draft
                base_output["draft"] = parsed
                base_output["warnings"] = check_draft(parsed, (j.input or {}).get("context") or {})

        elif j.kind == "submittal_review":
            err = _persist_submittal_review(db, j, result)
            if err is not None:
                j.status = "error"
                j.error = err
                j.output = base_output
                db.commit()
                return

        elif j.kind == "daily_report_capture":
            # Parsed into the output envelope rather than written to the report.
            # Nothing reaches daily_site_reports until a person confirms the
            # proposal — see services/daily_report_capture.py.
            from app.services.daily_report_capture import parse_capture_result
            try:
                base_output["proposal"] = parse_capture_result(result.text)
            except Exception as exc:  # noqa: BLE001
                j.status = "error"
                j.error = f"Could not read the proposal: {exc}"
                j.output = base_output
                db.commit()
                return

        elif j.kind == "change_order_generation":
            co_row, err = _persist_change_order_from_result(db, j, result)
            if err is not None:
                j.status = "error"
                j.error = err
                j.output = base_output
                db.commit()
                return
            assert co_row is not None
            base_output["change_order_id"] = co_row["id"]
            base_output["change_order_number"] = co_row["number"]
            base_output["change_order"] = co_row
            skill_raw_output = co_row

        elif j.kind == "ai_edit":
            plan, err = _build_edit_plan_from_result(input_payload, result)
            if err is not None:
                j.status = "error"
                j.error = err
                j.output = base_output
                db.commit()
                return
            # PROPOSED, not applied. The job's whole output is a change set a
            # person has to accept; applying from the worker would put an AI
            # write on a client document with nobody in the loop.
            base_output["edit_plan"] = plan
            skill_raw_output = plan

            # Record it as a PROPOSAL so a decision — including a decision not
            # to — survives. Before this table existed, declining an edit left
            # no trace at all and three of the four audit facts were lost.
            #
            # ⚠️ A failure here must not fail the job: the plan is the valuable
            # output and is already in base_output. But it must not fail
            # SILENTLY either, or the register quietly misses proposals and
            # looks complete. The reason is written into the output instead.
            try:
                from app.services import proposals as _proposals

                _proposals.supersede_pending(
                    db, j.project_id,
                    _proposals.TARGETS[plan["record_type"]]["table"],
                    plan["record_id"],
                )
                prop = _proposals.create(
                    db,
                    project_id=j.project_id,
                    kind=plan["record_type"],
                    target_id=plan["record_id"],
                    summary=(plan.get("summary")
                             or plan.get("instruction")
                             or "Proposed edit")[:500],
                    change_set=plan.get("changes") or [],
                    origin="ai_edit",
                    ai_job_id=j.id,
                )
                base_output["proposal_id"] = str(prop.id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("ai_worker: could not record proposal for job %s", j.id)
                base_output["proposal_error"] = f"{type(exc).__name__}: {exc}"

        elif j.kind == "document_summary":
            summary_id, err = _persist_document_summary_from_result(db, j, result)
            if err is not None:
                j.status = "error"
                j.error = err
                j.output = base_output
                db.commit()
                return
            base_output["document_summary_id"] = str(summary_id) if summary_id else None

        j.status = "done"
        j.output = base_output
        j.error = None
        j.finished_at = datetime.now(timezone.utc)
        # Persist the RESULT before anything optional runs. The only commit used
        # to live inside the telemetry block below, nested under
        # `if skill_id is not None`, so a kind absent from kind_to_skill was
        # marked done in memory, never committed, and the session rolled back on
        # close: the job sat in `running` forever with no error and no log line.
        # change_order_generation hit this on its first run. Telemetry is
        # best-effort; the job result is not.
        db.commit()

        # S1 — skill-engine telemetry. Maps the worker kind to its
        # registered skill_id, logs one SkillRun per successful job.
        # Best-effort: a missing skill row or telemetry write must never
        # fail the job. Future work (S2/S3) reads this surface to
        # generate candidates.
        try:
            from app.services.skill_engine import log_skill_run

            kind_to_skill = {
                "scope_analysis": "scope_analyzer",
                "estimate_generation": "estimate_generator",
                "document_summary": "document_summary",
            }
            skill_id = kind_to_skill.get(j.kind)
            if skill_id is not None:
                # Project type drives commercial-vs-residential eval split.
                proj_type = None
                try:
                    from app.models.project import Project

                    proj = db.get(Project, j.project_id)
                    proj_type = getattr(proj, "type", None) if proj else None
                except Exception:  # noqa: BLE001
                    pass
                log_skill_run(
                    db,
                    skill_id=skill_id,
                    ai_job=j,
                    project_type=proj_type,
                    input_payload=j.input or {},
                    raw_output=skill_raw_output if skill_raw_output is not None else j.output,
                    latency_ms=None,  # not tracked yet — measured per-call later
                    cost_usd=j.cost_usd,
                )
                db.commit()
        except Exception:  # noqa: BLE001 — never let telemetry break jobs
            pass


def _build_edit_plan_from_result(
    merged_input: dict[str, Any], result: AnthropicResult
) -> tuple[dict | None, str | None]:
    """Parse the model's change set, check it against the record, and return it.

    Nothing is written to the record here. Three filters run before a human
    ever sees the plan:

      · derived money fields are stripped, because a total is recomputed from
        the line items and a model-authored one would either be overwritten or
        survive and disagree with the lines beneath it;
      · every change is checked against the CURRENT document, so an invented
        path or a record that moved while the job queued is marked stale
        rather than applied over;
      · the resulting document is computed and returned alongside, so the
        review screen shows the actual outcome instead of a description of it.
    """
    from app.services.edit_plan import apply as apply_changes
    from app.services.edit_plan import verify
    from app.services.edit_prompt import strip_total_edits

    parsed, err = _parse_json_object(result)
    if err is not None:
        return None, err
    assert parsed is not None

    # The MERGED payload, not job.input — job.input never held the document.
    # It was loaded at run time so the plan is checked against the record as it
    # stands now, not as it stood when the job was queued.
    payload = merged_input or {}
    if payload.get("load_error"):
        return None, str(payload["load_error"])

    document = payload.get("document") or {}
    raw_changes = [c for c in (parsed.get("changes") or []) if isinstance(c, dict)]
    kept, dropped = strip_total_edits(raw_changes)
    checked = verify(document, kept)
    preview, applied = apply_changes(document, checked)

    return {
        "record_type": payload.get("record_type"),
        "record_id": str(payload.get("record_id") or ""),
        "instruction": payload.get("instruction"),
        "summary": parsed.get("summary"),
        "changes": checked,
        "applicable_count": len(applied),
        "blocked_count": len(checked) - len(applied),
        # What the model declined to do, plus what we declined on its behalf.
        "refused": list(parsed.get("refused") or []) + dropped,
        "preview": preview,
        "before": document,
    }, None


def _persist_change_order_from_result(
    db: Session, job: AIJob, result: AnthropicResult
) -> tuple[dict | None, str | None]:
    """Land the model's output as a DRAFT change order.

    Draft, always. A change order's value over a revised estimate is that it
    carries who approved the extra work and when, so generation must never be
    the thing that approves it. A person reads the figure and submits it.

    The breakdown, assumptions and exclusions are kept on the row's payload
    rather than thrown away: the number is a proposal, and the reasoning behind
    it is what makes it arguable with a client.
    """
    from decimal import Decimal, InvalidOperation

    from app.models.change_order import ChangeOrder

    parsed, err = _parse_json_object(result)
    if err is not None:
        return None, err
    assert parsed is not None

    title = (parsed.get("title") or "").strip()
    if not title:
        return None, "model returned no title for the change order"

    items = [i for i in (parsed.get("line_items") or []) if isinstance(i, dict)]

    def _dec(v: Any) -> Decimal:
        try:
            return Decimal(str(v or 0))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    cost = _dec(parsed.get("cost_impact"))
    summed = sum((_dec(i.get("amount")) for i in items), Decimal("0"))
    # The prompt requires these to agree. When they do not, the itemised sum is
    # the defensible number: it is the one that can be walked through with a
    # client line by line.
    if items and summed > 0 and abs(summed - cost) > Decimal("0.01"):
        cost = summed

    nxt = (
        db.execute(
            select(ChangeOrder.number)
            .where(ChangeOrder.project_id == job.project_id)
            .order_by(ChangeOrder.number.desc())
            .limit(1)
        ).scalar()
        or 0
    ) + 1

    try:
        days = int(parsed.get("schedule_impact_days") or 0)
    except (TypeError, ValueError):
        days = 0

    co = ChangeOrder(
        project_id=job.project_id,
        number=nxt,
        title=title[:200],
        description=(parsed.get("description") or "").strip() or None,
        cost_impact=cost,
        schedule_impact_days=days,
        status="draft",
        created_by_id=None,
        ai_job_id=job.id,
        # The working behind the number, on the change order itself. It used to
        # land only on ai_jobs.output, which the page never reads, so the figure
        # showed and the justification did not.
        detail={
            "line_items": items,
            "assumptions": parsed.get("assumptions") or [],
            "exclusions": parsed.get("exclusions") or [],
            "generated": True,
            "source_job_id": str(job.id),
            "instruction": (job.input or {}).get("instruction"),
        },
    )
    db.add(co)
    db.flush()
    return (
        {
            "id": str(co.id),
            "number": co.number,
            "title": co.title,
            "cost_impact": str(co.cost_impact),
            "schedule_impact_days": co.schedule_impact_days,
            "line_items": items,
            "assumptions": parsed.get("assumptions") or [],
            "exclusions": parsed.get("exclusions") or [],
        },
        None,
    )


def _persist_document_summary_from_result(
    db: Session, job: AIJob, result: AnthropicResult
) -> tuple[UUID | None, str | None]:
    """Parse the model's JSON output and upsert a DocumentSummary row.

    The row is identified by `document_id` (unique constraint). If the
    extraction context was `filename_only`, we accept a degenerate output
    with just `summary` and store empty structured_fields."""
    # Lazy import keeps the worker startup cheap when only scope/estimate
    # jobs are in the queue.
    from app.models.document_summary import DocumentSummary

    job_input = job.input or {}
    payload_document_id = (job_input.get("document") or {}).get("id") or job_input.get("document_id")
    if not payload_document_id:
        return None, "document_summary job missing document_id"

    try:
        doc_uuid = UUID(str(payload_document_id))
    except (TypeError, ValueError):
        return None, f"document_summary job has malformed document_id: {payload_document_id!r}"

    parsed, parse_err = _parse_json_object(result)
    if parse_err is not None:
        # Persist a failed summary so the UI can surface it, but flag the
        # job as errored so the worker retry path triggers if applicable.
        _upsert_summary_row(
            db,
            doc_uuid=doc_uuid,
            project_id=job.project_id,
            ai_job_id=job.id,
            status="failed",
            summary_text=None,
            structured_fields={},
            raw_extracted_text=(job_input.get("extraction") or {}).get("text") or None,
            error_message=parse_err,
        )
        return None, parse_err

    assert parsed is not None
    summary_text = (parsed.get("summary") or "").strip() or None
    structured = {k: v for k, v in parsed.items() if k != "summary"}

    row_id = _upsert_summary_row(
        db,
        doc_uuid=doc_uuid,
        project_id=job.project_id,
        ai_job_id=job.id,
        status="succeeded",
        summary_text=summary_text,
        structured_fields=structured,
        raw_extracted_text=(job_input.get("extraction") or {}).get("text") or None,
        error_message=None,
    )
    return row_id, None


def _upsert_summary_row(
    db: Session,
    *,
    doc_uuid: UUID,
    project_id: UUID,
    ai_job_id: UUID,
    status: str,
    summary_text: str | None,
    structured_fields: dict,
    raw_extracted_text: str | None,
    error_message: str | None,
) -> UUID:
    """Insert-or-update a DocumentSummary by document_id. Bumps
    indexed_at on success so the project-index renderer can detect the
    "stale cache" condition without scanning all rows."""
    from sqlalchemy import select

    from app.models.document_summary import DocumentSummary

    existing = db.execute(
        select(DocumentSummary).where(DocumentSummary.document_id == doc_uuid)
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if existing is None:
        row = DocumentSummary(
            document_id=doc_uuid,
            project_id=project_id,
            ai_job_id=ai_job_id,
            status=status,
            summary_text=summary_text,
            structured_fields=structured_fields,
            raw_extracted_text=raw_extracted_text,
            error_message=error_message,
            indexed_at=now if status == "succeeded" else None,
        )
        db.add(row)
        db.flush()
        return row.id

    existing.ai_job_id = ai_job_id
    existing.status = status
    existing.summary_text = summary_text
    existing.structured_fields = structured_fields
    existing.raw_extracted_text = raw_extracted_text
    existing.error_message = error_message
    if status == "succeeded":
        existing.indexed_at = now
    db.flush()
    return existing.id


def poll_once(*, client: anthropic.Anthropic | None = None) -> bool:
    """Claim and run at most one job. Returns True if a job ran."""
    with SessionLocal() as db:
        job = claim_one_job(db)
        job_id = job.id if job else None

    if job_id is None:
        return False
    run_job(job_id, client=client)
    return True


def main() -> int:  # pragma: no cover - process entry point
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    install_signal_handlers()
    logger.info(
        "ai_worker: starting (poll_interval=%.1fs, max_attempts=%d)",
        POLL_INTERVAL_SECONDS,
        MAX_ATTEMPTS,
    )
    with SessionLocal() as db:
        n = reap_orphaned_jobs(db, startup=True)
    if n:
        logger.warning("ai_worker: recovered %d job(s) abandoned by a previous worker", n)

    while not stop_requested():
        try:
            with SessionLocal() as db:
                reap_orphaned_jobs(db, startup=False)
            ran = poll_once()
            if not ran and not stop_requested():
                # Prepared documents the Mac did not pick up (API, under the cap).
                from app.services.prep_engine_api import run_one as run_prep

                ran = run_prep()
            if not ran and not stop_requested():
                # Idle: read one file's text for searching. AI jobs always go
                # first, so a reading backlog never delays a scope or estimate.
                from app.services.document_text_index import run_one

                ran = run_one()
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("ai_worker: unexpected error in poll loop")
            ran = False
        if not ran and not stop_requested():
            time.sleep(POLL_INTERVAL_SECONDS)
    logger.info("ai_worker: shutdown complete")
    return 0


def _reconcile_takeoff(db: Session, project_id: UUID, takeoff) -> dict | None:
    """Macro-reconcile a CAD takeoff against the project's PDF drawings — total
    area / floor count / door + window schedules. Best-effort: any failure returns
    a soft status and never blocks the takeoff itself."""
    try:
        import fitz  # pymupdf

        from app.services.drawing import reconcile as _rec
        from app.services.r2 import get_bytes as _get_bytes

        pdfs = (
            db.query(Document)
            .filter(Document.project_id == project_id, Document.archived.is_(False))
            .filter(Document.mime.ilike("%pdf%"))
            .order_by(Document.created_at.desc())  # newest sheets first for the [:3] cap
            .all()
        )
        text = ""
        for pd in pdfs[:3]:
            try:
                data = _get_bytes(pd.r2_key)
                d = fitz.open(stream=data, filetype="pdf")
                text += " ".join(" ".join(p.get_text().split()) for p in d) + "\n"
            except Exception:  # noqa: BLE001 — skip an unreadable PDF, keep going
                continue
        if not text.strip():
            return {"overall": "no_pdf", "checks": [], "flags": [],
                    "note": "No PDF drawings in the library to reconcile against."}
        proj = db.get(Project, project_id)
        macros = _rec.extract_pdf_macros(text, call_anthropic, model_for_kind("estimate_generation"))
        return _rec.reconcile(takeoff, macros, getattr(proj, "sqft", None))
    except Exception:  # noqa: BLE001 — reconciliation is advisory, never fatal
        logger.exception("ai_worker: cad_takeoff reconciliation failed for %s", project_id)
        return {"overall": "error", "checks": [], "flags": []}


def _ensure_cad_takeoff(db: Session, project_id) -> dict:
    """Phase 2/3 auto-branch: guarantee a CAD takeoff has run for this project's DWG/DXF
    drawings so the estimate can price from MEASURED quantities, and macro-reconcile it
    against the PDF. Idempotent — skips if cad_extract overrides already exist. Returns
    {available, reconciliation}: reconciliation is "pass"/"flagged"/etc. from the CAD↔PDF
    check (None when skipped), so the caller can gate a flagged discrepancy for review."""
    import os
    import tempfile

    from sqlalchemy import text as _text

    from app.services.drawing import cad_extractor as cad
    from app.services.r2 import get_bytes

    existing = db.execute(
        _text("SELECT COUNT(*) FROM quantity_overrides WHERE project_id=:p AND source='cad_extract'"),
        {"p": str(project_id)},
    ).scalar_one()
    if existing:
        return {"available": True, "reconciliation": None}

    # WHICH CAD files, and who decided. Until the document-selection screen
    # existed this was `category='drawing'`, which is a filing label and not a
    # decision: on 105-107 Maple it saw 4 of the 25 sheets the consultants
    # had stamped _DWG_, because the rest were filed under permit and survey.
    #
    # An explicit selection wins. `None` means nobody has used that screen on
    # this project yet, and the old category filter still applies — a silent
    # behaviour change on existing projects is how a working estimate becomes
    # an empty one.
    from app.services.document_selection import selected_ids

    chosen = selected_ids(db, project_id, purpose="quantities")
    if chosen is not None:
        if not chosen:
            return {"available": False, "reconciliation": None}
        cad_docs = db.execute(
            _text("SELECT id, filename, r2_key FROM documents WHERE project_id=:p "
                  "AND archived=false AND id::text = ANY(:ids) "
                  "AND (lower(filename) LIKE '%.dwg' OR lower(filename) LIKE '%.dxf')"),
            {"p": str(project_id), "ids": chosen},
        ).mappings().all()
    else:
        cad_docs = db.execute(
            _text("SELECT id, filename, r2_key FROM documents WHERE project_id=:p AND category='drawing' "
                  "AND archived=false AND (lower(filename) LIKE '%.dwg' OR lower(filename) LIKE '%.dxf')"),
            {"p": str(project_id)},
        ).mappings().all()
    if not cad_docs:
        return {"available": False, "reconciliation": None}

    any_written = False
    recon_overall: str | None = None
    for d in cad_docs:
        try:
            data = get_bytes(d["r2_key"])
            suffix = os.path.splitext(d["filename"] or "")[1].lower() or ".dwg"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
                tf.write(data)
                path = tf.name
            try:
                takeoff = cad.run_takeoff(path)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            written = cad.takeoff_to_overrides(db, project_id, takeoff, document_id=d["id"])
            recon = _reconcile_takeoff(db, project_id, takeoff) or {}
            if recon.get("overall") == "flagged":
                recon_overall = "flagged"          # any flagged sheet flags the whole set
            elif recon_overall is None:
                recon_overall = recon.get("overall")
            any_written = any_written or bool(written)
            logger.info("ai_worker: auto-takeoff %s → %s overrides (reconcile=%s)",
                        d["filename"], written, recon.get("overall"))
        except Exception:  # noqa: BLE001
            logger.exception("ai_worker: auto-takeoff failed for %s", d.get("filename"))
    db.commit()
    return {"available": any_written, "reconciliation": recon_overall}


def _run_cad_takeoff(db: Session, job: AIJob, input_payload: dict) -> None:
    """Deterministic CAD (DWG/DXF) quantity takeoff — no LLM/Anthropic call.

    Downloads the source drawing document, runs the scoped extractor
    (never counts the whole model), persists `quantity_overrides` CANDIDATES
    (source='cad_extract', for human reconcile+confirm before the estimate),
    and stores the scoped takeoff summary in `job.output`.
    """
    import os
    import tempfile
    from datetime import datetime, timezone

    from app.models.document import Document
    from app.services.drawing import cad_extractor as cad
    from app.services.r2 import get_bytes

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.attempts = (job.attempts or 0) + 1
    db.commit()
    try:
        doc_id = input_payload.get("document_id")
        doc = db.get(Document, doc_id) if doc_id else None
        if doc is None or doc.archived or doc.project_id != job.project_id:
            raise ValueError("cad_takeoff requires a live, in-project DWG/DXF document")
        data = get_bytes(doc.r2_key)
        suffix = os.path.splitext(doc.filename or "")[1].lower() or ".dwg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            tf.write(data)
            path = tf.name
        try:
            takeoff = cad.run_takeoff(path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        written = cad.takeoff_to_overrides(db, job.project_id, takeoff, document_id=doc.id)
        recon = _reconcile_takeoff(db, job.project_id, takeoff)
        job.output = {**cad.takeoff_summary(takeoff), "quantity_overrides_written": written,
                      "reconciliation": recon}
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("ai_worker: cad_takeoff job %s done (%s overrides, reconciliation=%s)",
                    job.id, written, (recon or {}).get("overall"))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        j = db.get(AIJob, job.id)
        if j is not None:
            j.status = "error"
            j.error = f"cad_takeoff: {exc}"[:500]
            j.finished_at = datetime.now(timezone.utc)
            db.commit()
        logger.exception("ai_worker: cad_takeoff job %s failed", job.id)


def _run_drawing_analysis(db: Session, job: AIJob, input_payload: dict) -> None:
    """Parse a drawing PDF for the measure page and store it (no AI call).

    Lives here, not in the web request, because the parse takes 15-20 s and
    ~600 MB; see services/drawing/analysis_queue.py.
    """
    from app.services.drawing.cache import get_or_analyze

    try:
        row = get_or_analyze(db, UUID(str(input_payload.get("document_id"))),
                             force=bool(input_payload.get("force")))
        if row is None:
            raise ValueError("document not found")
        j = db.get(AIJob, job.id)
        j.output = {"analysis_id": str(row.id), "page_count": row.page_count, "is_vector": row.is_vector}
        j.status = "done"
        j.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("ai_worker: drawing_analysis job %s done (%s pages)", job.id, row.page_count)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        j = db.get(AIJob, job.id)
        if j is not None:
            j.status = "error"
            j.error = f"drawing_analysis: {exc}"[:500]
            j.finished_at = datetime.now(timezone.utc)
            db.commit()
        logger.exception("ai_worker: drawing_analysis job %s failed", job.id)


def _ensure_every_division(cats: list[dict], labels: list[str] | None) -> list[dict]:
    """Impose the division set ONCE, on the assembled estimate.

    Each discipline now returns only what it priced, so the combined breakdown
    has gaps. The client breakdown should still show every division — absent
    scope staying visible is the point of the rule — so missing ones are added
    here at zero, in the set's own order.

    ⚠️ This is where completeness belongs. Asking each of six disciplines to pad
    out all 21 divisions produced 126 categories and put seven or eight
    divisions in more than one trade's hands.
    """
    if not labels:
        return cats
    present = {(c.get("name") or "").strip() for c in cats}
    for lab in labels:
        if lab not in present:
            cats.append({
                "name": lab, "discipline": None, "items": [],
                "subtotalRaw": 0, "contingencyAmount": 0, "total": 0,
                "description": "No scope identified for this division.",
            })
    order = {lab: i for i, lab in enumerate(labels)}
    # Unknown names sort after the set, keeping their relative order.
    return sorted(cats, key=lambda c: order.get((c.get("name") or "").strip(), len(order)))


def _contingency_for_project(db: Session, project_id) -> dict | None:
    """The division set's contingency percentages for a project, by division name.

    Returns None when the project resolves to no set — an unclassified project —
    in which case the model's own emission is all there is.
    """
    from sqlalchemy import text as _text
    from app.services.divisions import division_context

    # ⚠️ NO BLANKET `except Exception` HERE, and that is the whole point.
    #
    # The first version of this swallowed everything and returned None on
    # failure. It contained a NameError — `_text` is imported per-function in
    # this module, not at module level — so it raised on every call, returned
    # None, and None is indistinguishable from "this project has no division
    # set". The contingency silently fell back to 5% on divisions the set says
    # carry none, and the estimate looked fine: $468 charged on 880 Harbour where
    # the answer is $0.
    #
    # A resolver that fails open on client-facing money must fail LOUDLY. A
    # genuinely unmapped project is a different thing and returns None below,
    # explicitly.
    ptype = db.execute(
        _text("SELECT type FROM projects WHERE id = :p"), {"p": str(project_id)},
    ).scalar()
    ctx = division_context(db, ptype) or {}
    return ctx.get("contingency_pct") or None


def _contingency_pct_for(cat: dict, by_division: dict | None) -> float:
    """See estimate_totals.contingency_pct_for — the ONE copy of the rule."""
    from app.services.estimate_totals import contingency_pct_for

    return contingency_pct_for(cat, by_division)


def _rate_link_context(db: Session, project_id) -> tuple[dict, dict]:
    """(rate catalogue, signed takeoff quantities) for linking estimate lines."""
    from app.services.rate_links import catalog
    from app.services.rate_reconcile import _takeoff_quantities

    ptype = (db.execute(_sql_text("SELECT type FROM projects WHERE id = :p"),
                        {"p": str(project_id)}).scalar() or "").lower()
    scope = "commercial" if ptype.startswith("commercial") else (
        "residential" if ptype.startswith("residential") else "any")
    return catalog(db, scope), _takeoff_quantities(db, project_id)


def _rollup_category(cat: dict, contingency_by_division: dict | None = None) -> None:
    """Compute subtotalRaw / contingencyAmount / total for one category.

    Delegates to estimate_totals so generation and editing cannot compute
    money differently — they did until 2026-10-12. Line items when present;
    the category `total` when there are none (standard/summary detail).
    """
    from app.services.estimate_totals import rollup_category

    rollup_category(cat, contingency_by_division)


_ESTIMATE_FULL_DISCIPLINES = ["architectural", "structural", "mechanical",
                              "electrical", "plumbing", "gc_rollup"]

#: The most room one trade's answer may be given after it was cut off.
#: 2026-10-22: the architectural trade of a commercial TI at fully_itemized ran
#: past the 16k cap on two projects in a row.
ESTIMATE_DISCIPLINE_MAX_TOKENS = 32000


class DisciplineFailed(RuntimeError):
    """One trade produced no usable estimate. The whole run fails on it.

    `evidence` describes the last answer: its size, how it stopped, and its
    first and last characters. The answer itself is not kept anywhere else, and
    "cut off at 32k" alone cannot tell a long estimate from a model stuck
    repeating itself (2026-10-22: the cut started ~18.8k characters in).
    """

    def __init__(self, message: str, evidence: dict | None = None):
        super().__init__(message)
        self.evidence = evidence or {}


def _price_discipline(
    *, disc: str, model: str, system: str | None, messages: list[dict[str, Any]],
    max_tokens: int, approved: float | None, meter: dict,
) -> dict:
    """One trade's estimate as parsed JSON, or DisciplineFailed.

    Two attempts. The repair depends on how the first one failed (see
    _repair_directive): output cut off at the cap gets MORE room, because
    repeating the same call reproduces the same cut. Until 2026-10-22 the retry
    was identical to the first call, so a trade too big for the cap failed twice
    and was then dropped from an estimate that still reported done.
    """
    tokens = max_tokens
    msgs = messages
    err: str | None = None
    result = None
    for _attempt in range(2):
        if approved is not None and meter["usd"] >= Decimal(str(approved)):
            # A half-priced estimate is worse than none: stop, save nothing.
            raise RuntimeError(
                f"stopped before {disc}: it had spent ${meter['usd']:.2f} of the "
                f"${float(approved):.2f} you approved, and an estimate missing trades "
                "would read as complete. Nothing was saved.")
        result = _call_with_retry(model=model, system=system, messages=msgs, max_tokens=tokens)
        meter["in"] += result.input_tokens
        meter["out"] += result.output_tokens
        meter["usd"] += result.cost_usd
        parsed, err = _parse_json_object(result)
        if parsed and not err:
            return parsed
        if getattr(result, "stop_reason", None) == "max_tokens":
            if tokens >= ESTIMATE_DISCIPLINE_MAX_TOKENS:
                break
            tokens = min(max(tokens * 2, result.output_tokens * 2), ESTIMATE_DISCIPLINE_MAX_TOKENS)
            logger.info("ai_worker: estimate_full %s cut off at %d tokens; retrying with %d",
                        disc, result.output_tokens, tokens)
        else:
            instruction, _ = _repair_directive(err or "no JSON object", result)
            msgs = [*messages,
                    {"role": "assistant", "content": (result.text or "")[:4000]},
                    {"role": "user", "content": instruction}]
    text = (result.text if result is not None else "") or ""
    evidence = {
        "discipline": disc,
        "stop_reason": getattr(result, "stop_reason", None),
        "output_tokens": getattr(result, "output_tokens", None),
        "max_tokens": tokens,
        "chars": len(text),
        "head": text[:500],
        "tail": text[-1500:],
    }
    logger.warning("ai_worker: estimate_full %s unusable: %s", disc,
                   {k: v for k, v in evidence.items() if k not in ("head", "tail")})
    raise DisciplineFailed(f"{disc} produced no usable estimate after 2 attempts: {err}", evidence)


def _run_estimate_full(db: Session, job: AIJob, input_payload: dict) -> None:
    """Full multi-discipline estimate. Runs a per-discipline estimate for each
    trade + gc_rollup and COMBINES them into ONE estimate_version inserted as an
    inactive DRAFT (never deactivates existing versions).

    input:
      scope_id, mode, detail_level (default fully_itemized), overhead, profit,
      method: 'cad' (use CAD verified_quantities) | 'scope' (price from scope+rates only),
      disciplines (optional override list).
    """
    import json as _json
    from datetime import datetime, timezone
    from uuid import uuid4

    from sqlalchemy import text as _text

    from app.services.home import today as _vancouver_today

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.attempts = (job.attempts or 0) + 1
    db.commit()
    # Every call's tokens and cost. Until 2026-10-15 this job recorded $0 and no
    # tokens (ten runs on prod), so a $1-2 estimate never showed in any spend.
    meter = {"in": 0, "out": 0, "usd": Decimal(0)}
    approved = input_payload.get("approve_usd")

    def _record(j: AIJob) -> None:
        j.input_tokens, j.output_tokens, j.cost_usd = meter["in"], meter["out"], meter["usd"]
        j.model = model_for_kind("estimate_generation")

    try:
        scope_id = input_payload.get("scope_id")
        method = (input_payload.get("method") or "auto").lower()
        detail_level = input_payload.get("detail_level") or "fully_itemized"
        overhead = float(input_payload.get("overhead", 12))
        profit = float(input_payload.get("profit", 15))
        mode = input_payload.get("mode")  # None ⇒ _merge auto-detects from project.type
        disciplines = input_payload.get("disciplines") or _ESTIMATE_FULL_DISCIPLINES
        model = model_for_kind("estimate_generation")

        # Phase 2 auto-branch: pick the path from document readiness, and make sure a CAD
        # takeoff has actually run before we price from "measured" quantities. A silent
        # fall-back to rate pricing is now recorded in provenance, not hidden.
        from app.services.estimate_readiness import compute_readiness
        readiness = compute_readiness(db, job.project_id)
        if method == "auto":
            method = "cad" if readiness["takeoff_mode"] == "cad" else "scope"
        provenance = {"takeoff_mode": readiness["takeoff_mode"], "confidence": readiness["confidence"]}
        if method == "cad":
            takeoff = _ensure_cad_takeoff(db, job.project_id)
            if not takeoff["available"]:
                method = "scope"                  # CAD present but takeoff yielded nothing
                provenance["fell_back"] = True
            elif takeoff.get("reconciliation") == "flagged":
                # Phase 3 gate: takeoff disagrees materially with the PDF permit set —
                # keep the measured numbers but surface it for human review, don't trust silently.
                provenance["needs_review"] = True
                provenance["reconciliation"] = "flagged"
        provenance["method"] = method
        provenance["source"] = "cad_measured" if method == "cad" else "rate_scope"

        all_cats: list[dict] = []
        report: list[str] = []
        trade_assumptions: list[str] = []
        trade_exclusions: list[str] = []

        # Declared up front so a reader sees "2 of 6" rather than "2 so far, of
        # an unknown number" — during a seven-minute wait an unknown denominator
        # reads as no progress at all. Written on its OWN session; see
        # services/job_progress.py for why writing it on `db` would make every
        # line invisible until the job ended.
        _progress.plan(job.id, [*disciplines, "rate library", "assemble"])

        # The division set's own contingency policy, read once. Authoritative
        # over whatever the model emits — see _contingency_pct_for.
        contingency_by_division = _contingency_for_project(db, job.project_id)
        # Phase 3 linked pricing: every line is checked against, and priced
        # from, the library rate it cites. Read once for the whole job.
        _rl = _rate_link_context(db, job.project_id)
        link_totals: dict[str, float] = {}

        for disc in disciplines:
            _progress.begin(job.id, disc)
            disc_input = {"scope_id": scope_id, "detail_level": detail_level,
                          "discipline": disc}
            if mode:
                disc_input["mode"] = mode
            # ⚠️ PASS THROUGH, so an override on the JOB reaches the per-discipline
            # call. `disc_input` was built fresh from four keys, which meant
            # anything set on the job input was silently dropped — two controlled
            # experiments on 2026-10-06 changed nothing and looked like refutations
            # because of it. `_merge_estimate_inputs` only fills these when they
            # are None, so a supplied value (including `{}`) suppresses the block.
            for passthrough in ("rates", "divisions"):
                if passthrough in input_payload:
                    disc_input[passthrough] = input_payload[passthrough]
            merged = _merge_estimate_inputs(db, job.project_id, disc_input)
            if method == "scope":
                # scope-based estimate: price from the scope + rates only, do not
                # inject measured CAD quantities (the "old approach").
                merged["verified_quantities"] = []
            system, messages, max_tokens = build_messages_for_job("estimate_generation", merged)
            # ⚠️ A trade that cannot be priced FAILS THE RUN, nothing is saved.
            # Until 2026-10-22 it was skipped and the run still finished "done"
            # with a plausible total: on 456 W Sample both estimates lost their
            # whole architectural trade (walls, ceilings, floors, doors, millwork)
            # and the first one became the project's current estimate.
            try:
                parsed = _price_discipline(disc=disc, model=model, system=system,
                                           messages=messages, max_tokens=max_tokens,
                                           approved=approved, meter=meter)
            except DisciplineFailed as exc:
                _progress.finish(job.id, disc, ok=False, note=str(exc)[:200])
                raise
            cats = [c for c in (parsed.get("categories") or []) if isinstance(c, dict)]
            # Each trade's assumptions and exclusions — dropped until 2026-10-13,
            # when the finish answers made them carry real content ("Flooring
            # assumed: LVP; not confirmed"). De-duplicated across trades below.
            for _k, _bucket in (("assumptions", trade_assumptions), ("exclusions", trade_exclusions)):
                for _x in parsed.get(_k) or []:
                    if isinstance(_x, str) and _x.strip() and _x.strip() not in _bucket:
                        _bucket.append(_x.strip())
            if detail_level == "fully_itemized":
                from app.services.rate_links import link_categories

                for k, v in link_categories(cats, *_rl).items():
                    link_totals[k] = round(link_totals.get(k, 0) + v, 2)
            for cat in cats:
                _rollup_category(cat, contingency_by_division)
                cat["discipline"] = disc
                all_cats.append(cat)
            report.append(f"{disc}:{len(cats)}")
            _progress.finish(job.id, disc, note=f"{len(cats)} categories")

        # Track 2 Phase 6: deterministic rate-library reconciliation (last step,
        # after the LLM has priced everything). Flag-gated; advisory bindings are
        # non-destructive, override bindings replace a category from rate × takeoff
        # qty. Never touches the scope/estimate prompts.
        if link_totals:
            provenance["rate_links"] = link_totals
        _progress.begin(job.id, "rate library")
        rate_report: list[dict] = []
        if settings.use_rate_library:
            from app.services.rate_reconcile import apply_rate_library

            ptype = (_merge_project_metadata(db, job.project_id, {})
                     .get("project_metadata") or {}).get("project_type") or ""
            rl_ptype = "commercial" if "commercial" in ptype.lower() else (
                "residential" if "residential" in ptype.lower() else "any")
            all_cats, rate_report = apply_rate_library(
                db, all_cats, job.project_id,
                project_type=rl_ptype, rollup=_rollup_category,
            )
            if rate_report:
                provenance["rate_library"] = rate_report
        _progress.finish(
            job.id, "rate library",
            note=(f"{len(rate_report)} binding(s) reconciled" if rate_report
                  else "no bindings applied"),
        )

        _progress.begin(job.id, "assemble")
        # Completeness imposed once, here, rather than six times in the prompts.
        from app.services.divisions import division_context

        _ptype = db.execute(
            _text("SELECT type FROM projects WHERE id = :p"),
            {"p": str(job.project_id)}).scalar()
        _labels = (division_context(db, _ptype) or {}).get("labels")
        all_cats = _ensure_every_division(all_cats, _labels)
        provenance["division_set_applied"] = bool(_labels)
        subtotal = sum(c.get("total", 0) for c in all_cats)
        method_tag = "CAD" if method == "cad" else "Scope"
        combined = {
            "detailLevel": detail_level, "overhead": overhead, "profit": profit,
            "categories": all_cats,
            # Vancouver's date: at 5 pm or later, UTC is already tomorrow (the
            # same bug the scope date had; found 2026-10-15).
            "date": _vancouver_today().isoformat(), "validDays": 30,
            "method": method,
            "provenance": provenance,
            "notes": (f"Full multi-discipline estimate — {method_tag} method "
                      f"({'measured CAD quantities' if method == 'cad' else 'scope + rates'})"
                      f"{' — CAD present but takeoff empty, fell back to rates' if provenance.get('fell_back') else ''}. "
                      + " | ".join(report)),
            "assumptions": trade_assumptions, "exclusions": trade_exclusions,
            "paymentSchedule": [],
        }
        # Serialize version numbering + activation per project (avoids MAX+1 and
        # is_active races with a concurrent save or second job). Auto-releases at commit.
        db.execute(_text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": str(job.project_id)})
        next_ver = db.execute(
            _text("SELECT COALESCE(MAX(version_number),0)+1 FROM estimate_versions WHERE project_id=:p"),
            {"p": job.project_id}).scalar_one()
        # Auto-activate ONLY the first estimate (project has none active yet); every
        # later generation stays an inactive DRAFT so it can't disturb the current
        # active version.
        is_active = db.execute(
            _text("SELECT 1 FROM estimate_versions WHERE project_id=:p AND is_active=true LIMIT 1"),
            {"p": job.project_id}).scalar_one_or_none() is None
        short = str(job.project_id).replace("-", "")[:8].upper()
        ref = f"EST-AI-{short}-V{int(next_ver):03d}"
        combined["reference"] = ref
        from app.services.finish_spec import apply_for_project as _apply_finish

        _apply_finish(db, job.project_id, combined)
        new_version_id = uuid4()
        db.execute(_text(
            """INSERT INTO estimate_versions
               (id, project_id, version_number, reference, label, cost_basis,
                overhead_pct, profit_pct, notes, payload, parent_version_id,
                is_active, created_by_id, ai_job_id, scope_id)
               VALUES (:id,:p,:v,:ref,:label,'LOW',:oh,:pr,:notes,
                       CAST(:payload AS jsonb),NULL,:active,NULL,:ai_job,""" + NEW_VERSION_SCOPE_SQL + """)"""),
            {**scope_binds(job.project_id, None, scope_id), "id": new_version_id, "p": job.project_id, "v": int(next_ver), "ref": ref,
             "label": f"v{int(next_ver):03d} — {ref} ({method_tag}-based, {detail_level})",
             "oh": overhead, "pr": profit,
             "notes": (f"Full multi-discipline estimate — {method_tag} method."
                       + ("" if is_active else " DRAFT, inactive.")),
             "active": is_active,
             "ai_job": job.id,
             "payload": _json.dumps(combined)})
        # The id, so the Estimate page can open the draft it just made (the
        # sibling estimate_generation path always returned one; this did not).
        job.output = {"reference": ref, "version_number": int(next_ver), "is_active": is_active,
                      "estimate_version_id": str(new_version_id),
                      "method": method, "subtotal": subtotal, "disciplines": report}
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
        _record(job)
        db.commit()
        _progress.finish(job.id, "assemble", note=f"{len(all_cats)} categories")
        logger.info("ai_worker: estimate_full job %s done (%s, v%s, $%s)",
                    job.id, method, next_ver, subtotal)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        j = db.get(AIJob, job.id)
        if j is not None:
            j.status = "error"
            j.error = f"estimate_full: {exc}"[:500]
            if isinstance(exc, DisciplineFailed) and exc.evidence:
                # The error column holds 500 characters; the evidence goes on the
                # job output so a failed trade can be diagnosed after the fact.
                j.output = {"failed_discipline": exc.evidence}
            j.finished_at = datetime.now(timezone.utc)
            _record(j)  # a failed run still spent what it spent
            db.commit()
        logger.exception("ai_worker: estimate_full job %s failed", job.id)


def _run_presentation(db: Session, job: AIJob, input_payload: dict) -> None:
    """Client presentation deck. Pulls workbench data (numbers verbatim), runs
    ONE AI call for framing/justifications/approval-items, assembles the payload,
    and inserts a `presentations` row (draft). Never mutates estimates/schedule."""
    import json as _json
    from datetime import datetime, timezone
    from uuid import uuid4

    from sqlalchemy import text as _text

    from app.services import presentation as P
    from app.services.ai import compute_cost_usd, model_for_kind

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.attempts = (job.attempts or 0) + 1
    db.commit()
    try:
        audience = (input_payload.get("audience") or "client").lower()
        ctx = P.gather_context(db, job.project_id, input_payload.get("estimate_version_id"))
        # Allow a start-date override so owner-decision deadlines can be computed
        # even when the project has no CPM schedule loaded yet.
        ps = input_payload.get("project_start")
        if ps and not ctx["schedule"].get("projectStart"):
            ctx["schedule"]["projectStart"] = ps
        # Prior estimate the client already saw, for the "why higher" section.
        # Explicit input wins; otherwise fall back to the project's persisted value
        # so the section survives auto-regeneration.
        prior = input_payload.get("prior_estimate")
        if not prior:
            prior = db.execute(
                _text("SELECT prior_client_estimate FROM projects WHERE id = :p"),
                {"p": str(job.project_id)},
            ).scalar() or None
        ctx["priorEstimate"] = prior

        system, messages, max_tokens = P.build_presentation_messages(ctx)
        model = model_for_kind("scope_analysis")  # Opus — best prose + judgement
        ai = None
        err = None
        for _attempt in range(2):
            result = _call_with_retry(model=model, system=system, messages=messages, max_tokens=max_tokens)
            ai, err = _parse_json_object(result)
            if ai and not err:
                break
        if not ai:
            raise ValueError(f"AI framing returned no valid JSON: {err}")

        payload = P.assemble_payload(ctx, ai)

        next_ver = db.execute(_text(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM presentations "
            "WHERE project_id = :pid AND audience = :aud"),
            {"pid": str(job.project_id), "aud": audience}).scalar() or 1
        pres_id = uuid4()
        db.execute(_text(
            "INSERT INTO presentations (id, project_id, audience, version_number, label, status, "
            "  payload, based_on_estimate_id, based_on_schedule_id, based_on_cash_flow_id, ai_job_id, created_by_id) "
            "VALUES (:id, :pid, :aud, :ver, :label, 'draft', CAST(:payload AS jsonb), "
            "  :eid, :sid, :cid, :jid, :uid)"),
            {"id": str(pres_id), "pid": str(job.project_id), "aud": audience, "ver": int(next_ver),
             "label": input_payload.get("label") or f"Client Presentation v{next_ver}",
             "payload": _json.dumps(payload, default=str),
             "eid": ctx["estimate"]["id"], "sid": ctx["schedule"]["id"], "cid": ctx["cashFlow"]["id"],
             "jid": str(job.id), "uid": input_payload.get("created_by_id")})

        job.model = model
        job.input_tokens = result.input_tokens
        job.output_tokens = result.output_tokens
        job.cost_usd = compute_cost_usd(model, result.input_tokens, result.output_tokens)
        job.output = {"presentation_id": str(pres_id), "version_number": int(next_ver),
                      "audience": audience, "stale": ctx["stale"]}
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("ai_worker: presentation job %s done (v%s, %s)", job.id, next_ver, audience)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        j = db.get(AIJob, job.id)
        if j is not None:
            j.status = "error"
            j.error = f"presentation: {exc}"[:500]
            j.finished_at = datetime.now(timezone.utc)
            db.commit()
        logger.exception("ai_worker: presentation job %s failed", job.id)


def _run_scope_from_estimate(db: Session, job: AIJob, input_payload: dict) -> None:
    """Derive a scope-of-work FROM an estimate version (Mode 2). The estimate is
    the source of truth; the scope is regenerated to match it. Persists a new
    scopes row stamped with based_on_estimate_id."""
    import json as _json
    from datetime import datetime, timezone
    from uuid import uuid4

    from sqlalchemy import text as _text

    from app.services import scope_derive as SD
    from app.services.ai import compute_cost_usd, model_for_kind

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.attempts = (job.attempts or 0) + 1
    db.commit()
    try:
        pid = str(job.project_id)
        eid = input_payload.get("estimate_version_id")
        if eid:
            est = db.execute(_text("SELECT id, payload FROM estimate_versions WHERE id = :e AND project_id = :p"),
                             {"e": eid, "p": pid}).mappings().first()
        else:
            est = db.execute(_text("SELECT id, payload FROM estimate_versions WHERE project_id = :p AND is_active = true"),
                             {"p": pid}).mappings().first()
        if not est:
            raise ValueError("No estimate to derive a scope from.")
        est_payload = est["payload"]
        if isinstance(est_payload, str):
            est_payload = _json.loads(est_payload)
        project = db.execute(_text("SELECT name, address, type, sqft FROM projects WHERE id = :p"),
                             {"p": pid}).mappings().first() or {}

        system, messages, max_tokens = SD.build_scope_from_estimate_messages(est_payload, dict(project))
        model = model_for_kind("scope_analysis")  # Opus — scope quality
        ai = None
        err = None
        for _attempt in range(2):
            result = _call_with_retry(model=model, system=system, messages=messages, max_tokens=max_tokens)
            ai, err = _parse_json_object(result)
            if ai and not err:
                break
        if not ai:
            raise ValueError(f"scope-from-estimate returned no JSON: {err}")
        payload = SD.stamp(ai)

        next_ver = db.execute(_text("SELECT COALESCE(MAX(version), 0) + 1 FROM scopes WHERE project_id = :p"),
                              {"p": pid}).scalar() or 1
        sid = uuid4()
        db.execute(_text(
            "INSERT INTO scopes (id, project_id, version, payload, ai_job_id, based_on_estimate_id) "
            "VALUES (:id, :p, :v, CAST(:pl AS jsonb), :j, :e)"),
            {"id": str(sid), "p": pid, "v": int(next_ver), "pl": _json.dumps(payload),
             "j": str(job.id), "e": str(est["id"])})

        job.model = model
        job.input_tokens = result.input_tokens
        job.output_tokens = result.output_tokens
        job.cost_usd = compute_cost_usd(model, result.input_tokens, result.output_tokens)
        job.output = {"scope_id": str(sid), "version": int(next_ver), "based_on_estimate_id": str(est["id"])}
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("ai_worker: scope_from_estimate job %s done (scope v%s from est %s)",
                    job.id, next_ver, est["id"])
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        j = db.get(AIJob, job.id)
        if j is not None:
            j.status = "error"
            j.error = f"scope_from_estimate: {exc}"[:500]
            j.finished_at = datetime.now(timezone.utc)
            db.commit()
        logger.exception("ai_worker: scope_from_estimate job %s failed", job.id)


def _run_schedule_from_estimate(db: Session, job: AIJob, input_payload: dict) -> None:
    """AI-assisted CPM schedule FROM an estimate version. The model proposes the
    activity network; cpm.compute_cpm does the math. Persists schedule +
    activities stamped with based_on_estimate_id."""
    import json as _json
    from datetime import date as _date, datetime, timezone
    from uuid import uuid4

    from sqlalchemy import text as _text

    from app.services import schedule_derive as SCH
    from app.services.ai import compute_cost_usd, model_for_kind
    from app.services.cpm import compute_cpm

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.attempts = (job.attempts or 0) + 1
    db.commit()
    try:
        pid = str(job.project_id)
        eid = input_payload.get("estimate_version_id")
        if eid:
            est = db.execute(_text("SELECT id, payload FROM estimate_versions WHERE id = :e AND project_id = :p"),
                             {"e": eid, "p": pid}).mappings().first()
        else:
            est = db.execute(_text("SELECT id, payload FROM estimate_versions WHERE project_id = :p AND is_active = true"),
                             {"p": pid}).mappings().first()
        if not est:
            raise ValueError("No estimate to schedule from.")
        est_payload = est["payload"]
        if isinstance(est_payload, str):
            est_payload = _json.loads(est_payload)
        sc = db.execute(_text("SELECT payload FROM scopes WHERE project_id = :p AND based_on_estimate_id = :e "
                              "ORDER BY version DESC LIMIT 1"), {"p": pid, "e": str(est["id"])}).first()
        scope_payload = (sc[0] if sc else {}) or {}
        project = db.execute(_text("SELECT name, type, sqft FROM projects WHERE id = :p"), {"p": pid}).mappings().first() or {}

        system, messages, mt = SCH.build_schedule_from_estimate_messages(est_payload, scope_payload, dict(project))
        model = model_for_kind("scope_analysis")
        ai = None
        err = None
        for _attempt in range(2):
            result = _call_with_retry(model=model, system=system, messages=messages, max_tokens=mt)
            ai, err = _parse_json_object(result)
            if ai and ai.get("activities"):
                break
        if not ai or not ai.get("activities"):
            raise ValueError(f"schedule-from-estimate returned no activities: {err}")

        ps = input_payload.get("project_start")
        try:
            start_date = _date.fromisoformat(ps) if ps else datetime.now(timezone.utc).date()
        except Exception:
            start_date = datetime.now(timezone.utc).date()
        cpm = compute_cpm(ai["activities"], start_date)
        acts = cpm["activities"]

        next_ver = db.execute(_text("SELECT COALESCE(MAX(version_number), 0) + 1 FROM schedules WHERE project_id = :p"),
                              {"p": pid}).scalar() or 1
        sid = str(uuid4())
        db.execute(_text(
            "INSERT INTO schedules (id, project_id, version_number, project_start, working_hours, holidays, "
            " deterministic_finish_date, p50_finish_date, p80_finish_date, project_expected_wd, project_std_dev_wd, "
            " critical_path_activity_codes, notes, based_on_estimate_id) "
            "VALUES (:id,:p,:v,:ps,8,CAST('[]' AS jsonb),:fd50,:fd50,:fd80,:ewd,:std,CAST(:cpc AS jsonb),:notes,:e)"),
            {"id": sid, "p": pid, "v": int(next_ver), "ps": start_date.isoformat(),
             "fd50": cpm["p50_finish_date"], "fd80": cpm["p80_finish_date"],
             "ewd": cpm["project_expected_wd"], "std": cpm["project_std_dev_wd"],
             "cpc": _json.dumps(cpm["critical_path_activity_codes"]),
             "notes": f"AI-generated from estimate {est['id']}", "e": str(est["id"])})
        for a in acts:
            db.execute(_text(
                "INSERT INTO activities (id, schedule_id, activity_code, name, phase, predecessor_codes, lag_wd, "
                " optimistic_wd, most_likely_wd, pessimistic_wd, expected_wd, variance, early_start_wd, early_finish_wd, "
                " late_start_wd, late_finish_wd, total_float_wd, free_float_wd, start_date, finish_date, is_critical, is_milestone) "
                "VALUES (:id,:sid,:code,:name,:phase,CAST(:preds AS jsonb),0,:o,:ml,:pp,:exp,:var,:es,:ef,:ls,:lf,:tf,:ff,:sd,:fd,:crit,:ms)"),
                {"id": str(uuid4()), "sid": sid, "code": a["activity_code"], "name": a["name"], "phase": a.get("phase"),
                 "preds": _json.dumps(a.get("predecessor_codes") or []),
                 "o": a["optimistic_wd"], "ml": a["most_likely_wd"], "pp": a["pessimistic_wd"],
                 "exp": a["expected_wd"], "var": a["variance"],
                 "es": a["early_start_wd"], "ef": a["early_finish_wd"], "ls": a["late_start_wd"], "lf": a["late_finish_wd"],
                 "tf": a["total_float_wd"], "ff": a["free_float_wd"], "sd": a["start_date"], "fd": a["finish_date"],
                 "crit": a["is_critical"], "ms": a["is_milestone"]})

        job.model = model
        job.input_tokens = result.input_tokens
        job.output_tokens = result.output_tokens
        job.cost_usd = compute_cost_usd(model, result.input_tokens, result.output_tokens)
        job.output = {"schedule_id": sid, "version": int(next_ver), "activities": len(acts),
                      "finish_date": cpm["finish_date"], "based_on_estimate_id": str(est["id"])}
        job.status = "done"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("ai_worker: schedule_from_estimate job %s done (%s activities, finish %s)",
                    job.id, len(acts), cpm["finish_date"])
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        j = db.get(AIJob, job.id)
        if j is not None:
            j.status = "error"
            j.error = f"schedule_from_estimate: {exc}"[:500]
            j.finished_at = datetime.now(timezone.utc)
            db.commit()
        logger.exception("ai_worker: schedule_from_estimate job %s failed", job.id)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
