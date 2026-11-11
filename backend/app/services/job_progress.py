"""Telling someone what a long job is doing while it is still doing it.

⚠️ THE WHOLE DIFFICULTY IS THE TRANSACTION, and getting it wrong produces code
that looks right and reports nothing.

`_run_estimate_full` holds one session open across the entire run — 375 seconds
on average, 456 at the worst, measured in production. Progress written on THAT
session is invisible until it commits, which is when the job finishes. At that
point the progress is worthless: it reports the past of a thing that has ended.
Worse, committing that session early to flush progress would also commit half an
estimate.

So every write here opens its OWN short-lived session and commits immediately.
That is the only way a poller sees step 3 of 6 while step 3 is happening.

⚠️ PROGRESS MUST NEVER BREAK THE JOB. A failed status update is a cosmetic loss;
a failed estimate after seven minutes of model time is not. Every function here
swallows its own exceptions and logs. This is one of the few places where
silently continuing is right — and it is bounded, because the job's real outcome
is recorded by a different path entirely.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text as _T

from app.db import SessionLocal

logger = logging.getLogger("brio.job_progress")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


#: Overridable so tests can point this at the test database.
#:
#: ⚠️ WHY THIS SEAM EXISTS, and it is not for tidiness. `SessionLocal` binds to
#: DATABASE_URL. The test suite runs against TEST_DATABASE_URL and deliberately
#: keeps the two apart — the 2026-08-31 wipe happened because a test run reached
#: the database DATABASE_URL pointed at. So a service that opens its own session
#: is, under test, writing somewhere the fixtures do not control. In production
#: the two are the same database and nothing changes; under test the factory is
#: swapped and the writes land where they belong.
_session_factory = SessionLocal


def _write(job_id: UUID | str, mutate) -> None:
    """Apply `mutate(steps) -> steps` in a fresh session and commit at once."""
    try:
        with _session_factory() as s:
            row = s.execute(
                _T("SELECT steps FROM ai_jobs WHERE id = :i FOR UPDATE"),
                {"i": str(job_id)},
            ).first()
            if row is None:
                return
            steps = list(row[0] or [])
            steps = mutate(steps)
            s.execute(
                _T("UPDATE ai_jobs SET steps = CAST(:v AS jsonb), updated_at = now() "
                   "WHERE id = :i"),
                {"v": json.dumps(steps), "i": str(job_id)},
            )
            s.commit()
    except Exception:  # noqa: BLE001
        # Deliberate. See the module docstring: losing a progress line costs
        # nothing; losing the job costs seven minutes of model time.
        logger.exception("job_progress: could not record progress for %s", job_id)


def use_session_factory(factory) -> None:
    """Point progress writes at a different database. Tests only."""
    global _session_factory
    _session_factory = factory


def plan(job_id: UUID | str, names: list[str]) -> None:
    """Declare the steps up front, all pending.

    Declaring them before starting is what lets a reader see "2 of 6" rather
    than "2 so far, of an unknown number" — and an unknown denominator during a
    long wait reads as no progress at all.
    """
    def _m(_steps):
        return [{"name": n, "status": "pending", "started_at": None,
                 "finished_at": None, "note": None} for n in names]
    _write(job_id, _m)


def begin(job_id: UUID | str, name: str) -> None:
    def _m(steps):
        for st in steps:
            if st.get("name") == name:
                st.update(status="running", started_at=_now())
                return steps
        steps.append({"name": name, "status": "running", "started_at": _now(),
                      "finished_at": None, "note": None})
        return steps
    _write(job_id, _m)


def finish(job_id: UUID | str, name: str, *, note: str | None = None,
           ok: bool = True) -> None:
    def _m(steps):
        for st in steps:
            if st.get("name") == name:
                st.update(status="done" if ok else "error",
                          finished_at=_now(), note=note)
                return steps
        steps.append({"name": name, "status": "done" if ok else "error",
                      "started_at": None, "finished_at": _now(), "note": note})
        return steps
    _write(job_id, _m)


def summarise(steps: list[dict[str, Any]] | None) -> dict[str, Any]:
    """A one-line reading of where a job is. Used by the API and the UI.

    `current` is the step actually running, which is the thing a person waiting
    wants. It is None when nothing is running — either not started, or finished.
    """
    rows = list(steps or [])
    done = sum(1 for s in rows if s.get("status") == "done")
    failed = [s["name"] for s in rows if s.get("status") == "error"]
    running = next((s["name"] for s in rows if s.get("status") == "running"), None)
    return {
        "total": len(rows),
        "done": done,
        "current": running,
        "failed": failed,
        # No percentage when there are no steps: 0% implies a job that has not
        # started, which is different from a job that never reports steps.
        "percent": round(done / len(rows) * 100) if rows else None,
    }
