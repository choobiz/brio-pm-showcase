"""Ask — the assistant panel (UI round 1, phase 9). Read-only answers over the
same services the pages use; see app.services.assistant."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_office
from app.db import get_db
from app.services import assistant as A

router = APIRouter(prefix="/assistant", tags=["assistant"])


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    project_id: UUID | None = None
    history: list[Turn] = Field(default_factory=list, max_length=20)
    #: The cost the person saw and approved (Amir 2026-10-13); a hard limit.
    approve_usd: float = Field(gt=0, le=5)


@router.get("/estimate")
def estimate(db: Session = Depends(get_db), user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    from app.services import cost_estimate

    return cost_estimate.ask(db)


@router.post("/ask")
def ask(body: AskRequest, db: Session = Depends(get_db),
        user: CurrentUser = Depends(require_office)) -> dict[str, Any]:
    """Office staff only, like the rest of the office app. Every tool checks
    project access itself, so a manager sees only their own projects."""
    return A.ask(db, user, body.question.strip(),
                 project_id=str(body.project_id) if body.project_id else None,
                 history=[t.model_dump() for t in body.history], approved_usd=body.approve_usd)
