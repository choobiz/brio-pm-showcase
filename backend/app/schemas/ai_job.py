from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, computed_field

from app.schemas.common import TimestampedRead


# Derived from AI_JOB_KINDS rather than retyped. This list was maintained by
# hand and drifted twice: it silently 422'd `presentation`, and when that was
# fixed the note above was added and the list drifted again — by 2026-09-22 it
# was missing scope_from_estimate, schedule_from_estimate and
# change_order_generation, each of which the worker could run but the API would
# refuse to accept.
#
# Literal[] subscripted with a tuple is the same as subscripting with its
# members, so a kind added to the model is accepted here automatically and the
# two can no longer disagree.
from app.models.ai_job import AI_JOB_KINDS  # noqa: E402

AIJobKind = Literal[AI_JOB_KINDS]  # type: ignore[valid-type]
AIJobStatus = Literal["queued", "running", "done", "error"]


class AIJobBase(BaseModel):
    project_id: UUID
    kind: AIJobKind
    status: AIJobStatus = "queued"
    input: dict[str, Any] = {}
    output: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    attempts: int = 0
    #: Per-step progress on a long job. Empty for every kind that finishes in
    #: seconds — see migration 0063 for why this exists for estimate_full in
    #: particular.
    steps: list[dict[str, Any]] = []


class AIJobCreate(BaseModel):
    project_id: UUID
    kind: AIJobKind
    input: dict[str, Any] = {}


class AIJobRead(TimestampedRead, AIJobBase):
    @computed_field  # type: ignore[prop-decorator]
    @property
    def progress(self) -> dict[str, Any]:
        """Where a long job has got to — done/total, what is running now.

        ⚠️ COMPUTED FROM THE ONE DEFINITION, not reimplemented. The SSE stream
        already returned this; the REST read did not, and a client that polls
        would otherwise have had to summarise `steps` itself in TypeScript. Two
        implementations of "which step is running" would eventually disagree,
        and this codebase has already paid for a duplicated transform once.
        """
        from app.services.job_progress import summarise

        return summarise(self.steps)
