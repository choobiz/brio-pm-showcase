"""Scopes router — list / get / patch.

The "create" path is handled by the AI worker: it inserts a `Scope` row when
a `scope_analysis` AIJob succeeds. This router exposes the read + edit surface
for the review-before-save flow on the frontend.

Read responses carry a `source` block — the provenance of the scope (the notes
and images Keystone analyzed, plus the run's token/cost figures) — assembled from
the linked AIJob so the review UI can show how much input informed the scope.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, assert_project_access, get_current_user, require_office
from app.db import get_db
from app.models.ai_job import AIJob
from app.models.client import Client
from app.models.document import Document
from app.models.project import Project
from app.models.scope import Scope
from app.services.revisions import actor_uuid, snapshot
from app.schemas.scope import (
    ScopeSourceDocument,
    ScopePatch,
    ScopePdfResult,
    ScopeRead,
    ScopeSource,
    ScopeSourceImage,
)
from app.services.r2 import presign_get, put_bytes
from app.services.scope_pdf import render_full_scope_pdf, render_scope_pdf


router = APIRouter(prefix="/scopes", tags=["scopes"])


def _project_metadata(db: Session, project_id: UUID) -> dict:
    """Project context Keystone works from — mirrors the worker's
    `_merge_project_metadata` so the UI shows what actually fed the prompt."""
    project = db.get(Project, project_id)
    if project is None:
        return {}
    meta = {
        "name": project.name,
        "address": project.address,
        "project_type": project.type,
        "building_type": project.building_type,
        "building_age": project.building_age,
        "sqft": project.sqft,
    }
    return {k: v for k, v in meta.items() if v is not None}


def _build_source(db: Session, scope: Scope) -> ScopeSource | None:
    """Assemble the provenance block from the scope's originating AIJob."""
    if scope.ai_job_id is None:
        return None
    job = db.get(AIJob, scope.ai_job_id)
    if job is None:
        return None
    job_input = job.input or {}
    images = [
        ScopeSourceImage(caption=img.get("caption"), media_type=img.get("media_type"))
        for img in (job_input.get("images") or [])
        if isinstance(img, dict)
    ]
    notes = (job_input.get("notes") or "").strip() or None
    return ScopeSource(
        documents=_source_documents(db, scope.project_id, job_input.get("document_ids") or []),
        notes=notes,
        images=images,
        image_count=len(images),
        project_metadata=_project_metadata(db, scope.project_id),
        model=job.model,
        input_tokens=job.input_tokens,
        output_tokens=job.output_tokens,
        cost_usd=(str(job.cost_usd) if job.cost_usd is not None else None),
        generated_at=job.finished_at or job.created_at,
    )


def _source_documents(db: Session, project_id: UUID, ids: list) -> list[ScopeSourceDocument]:
    """Which ticked files the run could read, by the same rules the worker
    applies (`_merge_scope_inputs`): PDFs and images are sent as they are,
    Word / Excel / text are read as text, anything else is left out.
    """
    import os

    from app.services import doc_text

    out: list[ScopeSourceDocument] = []
    for raw in ids:
        try:
            doc = db.get(Document, UUID(str(raw)))
        except (ValueError, TypeError):
            continue
        if doc is None or doc.project_id != project_id:
            out.append(ScopeSourceDocument(id=str(raw), read=False,
                                           reason="removed from the project since"))
            continue
        ext = os.path.splitext(doc.filename or "")[1].lower()
        mime = (doc.mime or "").lower()
        if doc.archived:
            # Archived now; it may not have been when the scope was written.
            out.append(ScopeSourceDocument(id=str(doc.id), filename=doc.filename, read=True,
                                           reason="archived since"))
        elif mime == "application/pdf" or ext == ".pdf" or mime.startswith("image/") \
                or ext in doc_text.EXTRACTABLE_EXTS:
            out.append(ScopeSourceDocument(id=str(doc.id), filename=doc.filename))
        else:
            out.append(ScopeSourceDocument(
                id=str(doc.id), filename=doc.filename, read=False,
                reason=f"{ext or 'this file type'} can't be read for a scope"))
    return out


def _to_read(db: Session, scope: Scope) -> ScopeRead:
    return ScopeRead(
        id=scope.id,
        project_id=scope.project_id,
        version=scope.version,
        payload=scope.payload,
        ai_job_id=scope.ai_job_id,
        source=_build_source(db, scope),
        project=_project_metadata(db, scope.project_id),
        created_at=scope.created_at,
        updated_at=scope.updated_at,
    )


@router.get("", response_model=list[ScopeRead])
def list_scopes(
    project_id: UUID = Query(..., description="filter to scopes for this project"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[ScopeRead]:
    # Until 2026-10-12 no route here checked the project, and PATCH took any
    # signed-in user: a client account could rewrite any project's scope.
    assert_project_access(db, user, project_id)
    stmt = (
        select(Scope)
        .where(Scope.project_id == project_id)
        .order_by(Scope.version.desc())
    )
    return [_to_read(db, s) for s in db.execute(stmt).scalars().all()]


@router.get("/{scope_id}", response_model=ScopeRead)
def get_scope(
    scope_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ScopeRead:
    scope = db.get(Scope, scope_id)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scope not found")
    assert_project_access(db, user, scope.project_id)
    return _to_read(db, scope)


@router.patch("/{scope_id}", response_model=ScopeRead)
def update_scope(
    scope_id: UUID,
    payload: ScopePatch,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> ScopeRead:
    scope = db.get(Scope, scope_id)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scope not found")
    assert_project_access(db, user, scope.project_id)
    # The previous payload is gone the instant the next line runs. Record it
    # first, inside this transaction, so a rolled-back edit leaves no history
    # claiming it happened.
    snapshot(
        db, table="scopes", row_id=scope.id, project_id=scope.project_id,
        action="edit", actor_id=actor_uuid(user),
    )
    scope.payload = payload.payload
    db.commit()
    db.refresh(scope)
    return _to_read(db, scope)


@router.post("/{scope_id}/pdf", response_model=ScopePdfResult)
def generate_scope_pdf(
    scope_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> ScopePdfResult:
    """Render the scope's current payload to a branded PDF, store it in R2 as
    a `scope_pdf` Document, and return a short-lived download URL."""
    scope = db.get(Scope, scope_id)
    if scope is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scope not found")
    assert_project_access(db, user, scope.project_id)

    project = db.get(Project, scope.project_id)
    client = (
        db.get(Client, project.client_id)
        if project is not None and project.client_id is not None
        else None
    )
    project_dict = {
        "name": getattr(project, "name", None),
        "address": getattr(project, "address", None),
        "type": getattr(project, "type", None),
    }
    client_dict = {
        "name": getattr(client, "name", None),
        "email": getattr(client, "email", None),
        "phone": getattr(client, "phone", None),
        "address": getattr(client, "address", None),
    }

    pdf_bytes = render_scope_pdf(
        payload=scope.payload or {}, project=project_dict, client=client_dict
    )

    key = f"scopes/{scope_id}/scope-v{scope.version}.pdf"
    put_bytes(key, pdf_bytes, "application/pdf")

    filename = f"Scope of Work - {project_dict['name'] or 'Project'} v{scope.version}.pdf"
    doc = Document(
        project_id=scope.project_id,
        category="scope_pdf",
        phase="preconstruction",
        filename=filename,
        r2_key=key,
        mime="application/pdf",
        size_bytes=len(pdf_bytes),
        uploaded_by_id=UUID(user.id) if getattr(user, "id", None) else None,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return ScopePdfResult(
        document_id=doc.id,
        filename=filename,
        size_bytes=len(pdf_bytes),
        download_url=presign_get(key),
    )


def _collect_project_scopes(
    db: Session, project_id: UUID
) -> tuple[Project, list[Scope], dict, dict, list[dict]]:
    """Shared prep for the full + client PDF endpoints. Returns the project
    row, the selected (deduped, version-sorted) Scope rows, a project dict,
    a client dict, and the scope_inputs ready to hand to the renderer."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )

    stmt = (
        select(Scope)
        .where(Scope.project_id == project_id)
        .order_by(Scope.version.desc(), Scope.created_at.desc())
    )
    all_scopes = list(db.execute(stmt).scalars().all())
    if not all_scopes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no scopes on this project yet",
        )

    # Dedup by trade key (payload-derived) so the latest version of each
    # trade wins. A scope with no trade key is the whole-project scope, and
    # only its latest version counts. (Falling back to the scope id, as this
    # did until 2026-10-12, merged EVERY version of an untagged scope into one
    # document — latent while every project had one version, live the moment
    # a second was written.)
    seen: set[str] = set()
    selected: list[Scope] = []
    for s in all_scopes:
        p = s.payload if isinstance(s.payload, dict) else {}
        key = (
            p.get("trade")
            or p.get("category")
            or p.get("title")
            or p.get("name")
            or p.get("discipline")
            or "__whole_project__"
        )
        norm = str(key).strip().lower()
        if norm in seen:
            continue
        seen.add(norm)
        selected.append(s)
    selected.sort(key=lambda s: (s.version or 0, s.created_at))

    client = (
        db.get(Client, project.client_id) if project.client_id is not None else None
    )
    project_dict = {
        "name": getattr(project, "name", None),
        "address": getattr(project, "address", None),
        "type": getattr(project, "type", None),
        "sqft": getattr(project, "sqft", None),
    }
    client_dict = {
        "name": getattr(client, "name", None),
        "email": getattr(client, "email", None),
        "phone": getattr(client, "phone", None),
        "address": getattr(client, "address", None),
    }
    scope_inputs = [
        {
            "payload": s.payload or {},
            "version": s.version,
            "generated_at": s.created_at,
        }
        for s in selected
    ]
    return project, selected, project_dict, client_dict, scope_inputs


@router.post("/full-pdf", response_model=ScopePdfResult)
def generate_full_scope_pdf(
    project_id: UUID = Query(..., description="project to consolidate all scopes for"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> ScopePdfResult:
    """Render the project-wide unified Scope of Work — one cohesive
    industry-standard SOW document (project information → overview →
    numbered scope sections → materials → permits → site logistics →
    assumptions → exclusions → risk considerations). All per-trade scope
    data is merged into the appropriate section so the document reads as
    a single project, not six independent sub-scopes. Stored in R2 as a
    `scope_pdf` Document; returns a short-lived presigned download URL."""
    assert_project_access(db, user, project_id)
    _, selected, project_dict, client_dict, scope_inputs = _collect_project_scopes(
        db, project_id
    )

    pdf_bytes = render_full_scope_pdf(
        scopes=scope_inputs, project=project_dict, client=client_dict
    )

    timestamp = max((s.updated_at or s.created_at) for s in selected)
    key = (
        f"scopes/full/{project_id}/full-scope-"
        f"{timestamp.strftime('%Y%m%dT%H%M%S')}.pdf"
    )
    put_bytes(key, pdf_bytes, "application/pdf")

    safe_name = (project_dict["name"] or "Project").strip()
    filename = f"Scope of Work - {safe_name}.pdf"
    doc = Document(
        project_id=project_id,
        category="scope_pdf",
        phase="preconstruction",
        filename=filename,
        r2_key=key,
        mime="application/pdf",
        size_bytes=len(pdf_bytes),
        uploaded_by_id=UUID(user.id) if getattr(user, "id", None) else None,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return ScopePdfResult(
        document_id=doc.id,
        filename=filename,
        size_bytes=len(pdf_bytes),
        download_url=presign_get(key),
    )


# NOTE: The scope-based client-presentation PDF was removed 2026-06-17.
# Client-facing material is now the Investment Proposal generated from the
# estimate version (see `workbench.py` → estimate_version_client_pdf).
