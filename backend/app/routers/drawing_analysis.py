"""Drawing-analysis pack API — the review an estimate has to pass through.

Reading is open to anyone on the project. Building and approving are office-only
(manager and above): approving is the act that lets a machine-read number reach a
client price, and that is not a field-app permission.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user, require_office
from app.db import get_db
from app.models.drawing_analysis_pack import DrawingAnalysisPack
from app.models.project import Project

router = APIRouter(prefix="/projects/{project_id}/drawing-analysis",
                   tags=["drawing-analysis"])


class PackRead(BaseModel):
    id: UUID
    project_id: UUID
    version_number: int
    status: str
    payload: dict[str, Any]
    source_document_ids: list[Any] | None
    approved_by_id: UUID | None
    approved_at: datetime | None
    created_at: datetime


def _latest(db: Session, project_id: UUID) -> DrawingAnalysisPack | None:
    return db.execute(
        select(DrawingAnalysisPack)
        .where(DrawingAnalysisPack.project_id == project_id)
        .order_by(DrawingAnalysisPack.version_number.desc())
    ).scalars().first()


@router.get("", response_model=dict)
def read_analysis(project_id: UUID, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(get_current_user)) -> dict:
    """The latest pack, the gate state, and the quantities awaiting review."""
    from app.services.quantity_gate import gate_status

    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    # Until 2026-10-12 none of these routes checked the project: any signed-in
    # user, a client included, could read any project's analysis.
    assert_project_access(db, user, project_id)
    pack = _latest(db, project_id)
    g = gate_status(db, project_id)
    return {
        "pack": PackRead.model_validate(pack, from_attributes=True).model_dump()
        if pack else None,
        "gate": g.as_dict(),
        # Approval is a separate act from review: the numbers can all be
        # confirmed and still nobody has signed the analysis as a whole.
        "can_approve": bool(pack) and pack.status == "draft" and not g.open,
    }


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
def build_analysis(project_id: UUID, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_office)) -> dict:
    """Read the drawings and produce a new draft pack.

    Any existing approved pack is SUPERSEDED — a new reading of the drawings
    invalidates a sign-off that was given against the previous one.
    """
    from app.services.drawing_analysis import build_pack

    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    assert_project_access(db, user, project_id)
    payload = build_pack(db, project_id)
    prev = _latest(db, project_id)
    db.execute(
        DrawingAnalysisPack.__table__.update()
        .where(DrawingAnalysisPack.project_id == project_id,
               DrawingAnalysisPack.status == "approved")
        .values(status="superseded"))
    pack = DrawingAnalysisPack(
        project_id=project_id,
        version_number=(prev.version_number + 1) if prev else 1,
        status="draft", payload=payload,
        source_document_ids=payload.get("source_document_ids") or [])
    db.add(pack)
    db.commit()
    db.refresh(pack)
    return read_analysis(project_id, db, user)


class ApproveBody(BaseModel):
    note: str | None = None


@router.post("/approve", response_model=dict)
def approve_analysis(project_id: UUID, body: ApproveBody | None = None,
                     db: Session = Depends(get_db),
                     user: CurrentUser = Depends(require_office)) -> dict:
    """Sign the analysis off. Refuses while any quantity is still a candidate."""
    from app.services.quantity_gate import assert_quantities_reviewed

    assert_project_access(db, user, project_id)
    pack = _latest(db, project_id)
    if pack is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="no analysis to approve — build one first")
    if pack.status == "approved":
        return read_analysis(project_id, db, user)
    # The QUANTITY half only. The other half is that the pack is approved, and
    # this is the call that approves it — asserting it here made the button
    # refuse itself.
    assert_quantities_reviewed(db, project_id)  # raises 409 with the outstanding list
    pack.status = "approved"
    pack.approved_by_id = UUID(user.id) if user.id else None
    pack.approved_at = datetime.now(timezone.utc)
    if body and body.note:
        pack.payload = {**(pack.payload or {}), "approval_note": body.note}
    db.commit()
    return read_analysis(project_id, db, user)


@router.get("/sheet/{document_id}/view", response_model=dict)
def sheet_view(project_id: UUID, document_id: UUID, view: int | None = None,
               highlight: str | None = None, db: Session = Depends(get_db),
               user: CurrentUser = Depends(get_current_user)) -> dict:
    """The drawing itself, beside the numbers, with one quantity's source marked.

    Confirming "7 windows" from the digit alone is not review — it is assent. A
    reviewer needs to see WHICH seven, on the drawing, which is what `highlight`
    marks. Reading is open to anyone on the project: looking at a drawing is not
    the act that lets a number reach a client price.
    """
    import os
    import tempfile

    from sqlalchemy import text as _T

    from app.services.drawing.plan_svg import render_cached
    from app.services.r2 import get_bytes

    assert_project_access(db, user, project_id)
    doc = db.execute(_T(
        "SELECT id, filename, r2_key FROM documents "
        "WHERE id = :d AND project_id = :p AND archived = false"),
        {"d": str(document_id), "p": str(project_id)}).mappings().first()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="drawing not found on this project")

    suffix = os.path.splitext(doc["filename"] or "")[1].lower() or ".dwg"
    path = None

    def fetch() -> str:
        nonlocal path
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            tf.write(get_bytes(doc["r2_key"]))
            path = tf.name
        return path

    try:
        # Keyed on the stored object: a re-upload is a new r2_key, so a cached
        # read can never describe different bytes.
        out = render_cached(f'{doc["id"]}:{doc["r2_key"]}', fetch,
                            view_index=view, highlight=highlight)
    except Exception as exc:  # noqa: BLE001 — a drawing that will not render is
        # a legible answer, not a 500: the reviewer still has the numbers.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"could not render this drawing: {type(exc).__name__}: {exc}"[:200],
        ) from exc
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    return {"filename": doc["filename"], "highlight": highlight, **out}
