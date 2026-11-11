"""Drawing analysis runs in the background worker, never in a web request.

2026-10-26, 456 W Sample: parsing the corridor drawing set takes 15-20 s and
~600 MB. Done inside GET /drawings/{id}/analysis, the API worker handling it
was killed by uvicorn's supervisor twice, the page showed "API 502", and since
nothing was ever saved every visit started the same parse again.

The endpoint now returns the stored analysis, or queues one job per drawing
and says it is analysing; the measure page polls until the result is stored.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.ai_job import AIJob
from app.models.document import Document

KIND = "drawing_analysis"


def latest_job(db: Session, document_id: UUID) -> AIJob | None:
    return db.execute(
        select(AIJob)
        .where(AIJob.kind == KIND, AIJob.input["document_id"].astext == str(document_id))
        .order_by(AIJob.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def queue_analysis(db: Session, document: Document, *, force: bool = False) -> AIJob:
    """One queued/running job per drawing: a second caller gets the same job.
    Does not commit; the caller does."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{KIND}:{document.id}"})
    job = latest_job(db, document.id)
    if job is not None and job.status in ("queued", "running"):
        return job
    job = AIJob(project_id=document.project_id, kind=KIND,
                input={"document_id": str(document.id), "force": force})
    db.add(job)
    db.flush()
    return job


def is_drawing_pdf(document: Document) -> bool:
    name = (document.filename or "").lower()
    return document.category == "drawing" and ((document.mime or "") == "application/pdf" or name.endswith(".pdf"))
