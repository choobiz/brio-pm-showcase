"""Build an `AIJob(kind=document_summary)` from a Document row.

Called from the upload paths (presign-finalize + ZIP extract) to fire
auto-indexing right after a document lands in R2. Also called by the
manual reindex endpoints. Returns the queued AIJob without committing
— the caller commits as part of its own transaction.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_job import AIJob
from app.models.client import Client
from app.models.document import Document
from app.models.document_summary import DocumentSummary
from app.models.project import Project
from app.services.document_summary_extractor import (
    ExtractionContext,
    extract_for_summary,
)
from app.services.r2 import get_bytes


# Categories we never auto-index. Generated artifacts (index file itself,
# rendered scope PDFs) shouldn't re-enter the indexing loop.
_SKIP_CATEGORIES = {"project_index", "scope_pdf"}

# Hard byte cap. Anything bigger than this won't fit cleanly through the
# extractor + Anthropic payload. Skipped for now; future work can chunk.
_MAX_BYTES_FOR_INDEXING = 25 * 1024 * 1024  # 25 MB


def queue_document_summary(
    db: Session,
    document: Document,
    *,
    force: bool = False,
) -> AIJob | None:
    """Enqueue a document_summary job. Returns the job, or None if the
    document was skipped (already indexed, wrong category, oversized, or
    missing bytes). Caller must commit() afterwards."""

    if document.archived:
        return None
    if document.category in _SKIP_CATEGORIES:
        return None
    # Guardrail #1 — per-project opt-out for AUTOMATIC indexing (on upload).
    # A manual re-index passes force=True; that's an explicit user action, so
    # it bypasses the opt-out. The flag therefore governs auto-indexing only,
    # letting the owner index projects on demand while uploads stay silent.
    if not force:
        project = db.get(Project, document.project_id)
        if project is not None and not getattr(project, "index_documents", True):
            _mark_skipped(
                db,
                document,
                reason="project.index_documents = false (auto-index off; reindex to force)",
            )
            return None
    if document.size_bytes and document.size_bytes > _MAX_BYTES_FOR_INDEXING:
        # Mark a "skipped" row so the index UI reflects why.
        _mark_skipped(
            db,
            document,
            reason=f"oversized ({document.size_bytes} bytes > "
            f"{_MAX_BYTES_FOR_INDEXING} byte cap)",
        )
        return None

    # If a summary already exists and we're not forcing, skip.
    if not force:
        existing = db.execute(
            select(DocumentSummary).where(
                DocumentSummary.document_id == document.id
            )
        ).scalar_one_or_none()
        if existing is not None and existing.status in ("succeeded", "running", "queued"):
            return None

    # Pull bytes from R2 → extraction context.
    try:
        data = get_bytes(document.r2_key)
    except Exception as e:  # noqa: BLE001
        _mark_failed(db, document, error=f"R2 fetch failed: {e}")
        return None

    ctx = extract_for_summary(
        filename=document.filename,
        mime=document.mime,
        data=data,
    )

    project_context = _project_context(db, document.project_id)
    job_input: dict[str, Any] = {
        "document_id": str(document.id),
        "project_context": project_context,
        "document": {
            "id": str(document.id),
            "filename": document.filename,
            "category": document.category,
            "mime": document.mime,
            "size_bytes": document.size_bytes,
        },
        "extraction": {
            "mode": ctx.mode,
            "text": ctx.text,
            "image_b64": ctx.image_b64,
            "image_media_type": ctx.image_media_type,
            "notes": ctx.notes,
        },
    }

    job = AIJob(
        project_id=document.project_id,
        kind="document_summary",
        input=job_input,
    )
    db.add(job)
    db.flush()  # populate job.id for the in-progress summary row

    # Mark a "queued" summary row immediately so the Documents UI can
    # show a badge before the worker picks the job up.
    _upsert_summary_status(
        db,
        document=document,
        ai_job_id=job.id,
        status="queued",
        error=None,
    )

    return job


def _project_context(db: Session, project_id) -> dict[str, Any]:
    project = db.get(Project, project_id)
    if project is None:
        return {}
    client = (
        db.get(Client, project.client_id) if project.client_id is not None else None
    )
    return {
        "name": project.name,
        "address": project.address,
        "type": project.type,
        "sqft": project.sqft,
        "client_name": client.name if client else None,
    }


def _upsert_summary_status(
    db: Session,
    *,
    document: Document,
    ai_job_id: uuid.UUID | None,
    status: str,
    error: str | None,
) -> None:
    existing = db.execute(
        select(DocumentSummary).where(
            DocumentSummary.document_id == document.id
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            DocumentSummary(
                document_id=document.id,
                project_id=document.project_id,
                ai_job_id=ai_job_id,
                status=status,
                error_message=error,
            )
        )
    else:
        existing.status = status
        existing.ai_job_id = ai_job_id
        existing.error_message = error


def _mark_skipped(db: Session, document: Document, *, reason: str) -> None:
    _upsert_summary_status(
        db,
        document=document,
        ai_job_id=None,
        status="skipped",
        error=reason,
    )


def _mark_failed(db: Session, document: Document, *, error: str) -> None:
    _upsert_summary_status(
        db,
        document=document,
        ai_job_id=None,
        status="failed",
        error=error,
    )
