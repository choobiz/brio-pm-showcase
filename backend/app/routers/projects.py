"""Projects router — list / get / create.

A project is the spine everything else hangs off (documents, scope, estimate,
invoice). Create derives a unique slug from the name.
"""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    CurrentUser,
    accessible_project_ids,
    assert_project_access,
    get_current_user,
    require_office,
)
from app.db import get_db
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.schemas.project import ProjectCreateRequest, ProjectRead, ProjectUpdate
from app.services import clients as clients_svc

router = APIRouter(prefix="/projects", tags=["projects"])


def _actor_uuid(user: CurrentUser) -> UUID | None:
    """The acting user's id, or None if absent/not a UUID. Deliberately does not
    raise (unlike the copies in documents/invoices): this runs on the create path,
    and a malformed id must cost the membership row, not the whole project."""
    raw = getattr(user, "id", None)
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except (ValueError, TypeError):
        return None


def _unique_slug(db: Session, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "project").lower()).strip("-") or "project"
    slug = base
    n = 2
    while db.execute(select(Project.id).where(Project.slug == slug)).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _for_role(user: CurrentUser, project: Project) -> ProjectRead | Project:
    """Crew and client accounts get the project without the office's links and
    notes: the Google Drive folder opens every file on the job, and discussion
    points are the office's talking points with the client (rollout test,
    2026-10-15, as the test crew member)."""
    if user.role in ("manager", "superadmin"):
        return project
    return ProjectRead.model_validate(project, from_attributes=True).model_copy(
        update={"google_drive_url": None, "discussion_points": []})


@router.get("", response_model=list[ProjectRead])
def list_projects(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[Project]:
    # Scoped to the caller. Until 2026-10-12 this returned every project to any
    # signed-in user, the client (owner role) included.
    ids = accessible_project_ids(db, user)
    if not ids:
        return []
    stmt = select(Project).where(Project.id.in_(ids)).order_by(Project.created_at.desc())
    return [_for_role(user, p) for p in db.execute(stmt).scalars().all()]


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> Project:
    assert_project_access(db, user, project_id)
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return _for_role(user, project)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> Project:
    project = Project(
        slug=_unique_slug(db, body.name),
        name=body.name,
        address=body.address,
        type=body.type,
        status=body.status,
        client_id=body.client_id,
        building_age=body.building_age,
        building_type=body.building_type,
        sqft=body.sqft,
    )
    db.add(project)
    db.flush()  # need project.id for the membership row, same transaction
    clients_svc.mark_as_client(db, body.client_id)

    # Make the creator a member. `assert_project_access` admits only superadmin
    # without a ProjectMember row, so a project created by a manager was
    # unreachable to its own creator — every gated sub-resource (documents,
    # communications, workbench) returned 403 until someone assigned membership
    # by hand via /admin/users. Superadmins get a row too: harmless, and it keeps
    # the project reachable if their role is ever downgraded.
    creator_id = _actor_uuid(user)
    if creator_id is not None:
        db.add(ProjectMember(project_id=project.id, user_id=creator_id, role="member"))

    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/notify-client")
def notify_client(
    project_id: UUID,
    body: dict = Body(default={}),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """Send the client a curated update email (new photos / milestone / note)."""
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    assert_project_access(db, user, project_id)
    from app.services.notify import notify_owner_update, _owner_emails
    kind = str(body.get("kind") or "update")
    notify_owner_update(db, project_id, kind=kind, message=body.get("message"))
    return {"ok": True, "recipients": len(_owner_emails(db, project_id))}


@router.get("/{project_id}/getting-started")
def getting_started(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """New-project setup guide: next step + type-tailored document checklist."""
    assert_project_access(db, user, project_id)
    from app.services.getting_started import compute_getting_started
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return compute_getting_started(db, str(project_id))


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> Project:
    """Partial update — only fields supplied in the body get touched."""
    assert_project_access(db, user, project_id)
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    data = body.model_dump(exclude_unset=True)
    # NOT NULL column: an explicit null means "no change", {} means "clear".
    if data.get("drawing_decisions", {}) is None:
        data.pop("drawing_decisions")
    for k, v in data.items():
        setattr(project, k, v)
    if data.get("client_id"):
        clients_svc.mark_as_client(db, data["client_id"])
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/archive", response_model=ProjectRead)
def archive_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> Project:
    """Archive a project, retaining its current status so Unarchive can restore
    it exactly. No-op fields if it's already archived."""
    assert_project_access(db, user, project_id)
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    if project.status != "archived":
        project.pre_archive_status = project.status
        project.status = "archived"
        db.commit()
        db.refresh(project)
    return project


@router.post("/{project_id}/unarchive", response_model=ProjectRead)
def unarchive_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> Project:
    """Restore an archived project to the status it held before archiving
    (falling back to 'lead' if none was retained), and clear the retained value."""
    assert_project_access(db, user, project_id)
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    if project.status == "archived":
        project.status = project.pre_archive_status or "lead"
        project.pre_archive_status = None
        db.commit()
        db.refresh(project)
    return project


@router.post("/{project_id}/discussion-points/suggest")
def suggest_discussion_points(
    project_id: UUID,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_office),
) -> dict:
    """AI-draft client-discussion points from the project's own context (index +
    scope + active estimate). Returns drafts for the operator to review/edit —
    it does NOT save them. Grounded strictly in project data; returns [] if
    there isn't enough to summarise."""
    assert_project_access(db, user, project_id)
    import json as _json

    from sqlalchemy import text as _text

    from app.services.ai import MODEL_SONNET, call_anthropic

    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    parts: list[str] = [
        f"Project: {project.name or ''}",
        f"Type: {project.type or ''}",
        f"Address: {project.address or ''}",
    ]
    try:
        from app.services.project_index import render_project_index_markdown

        index_md, _meta = render_project_index_markdown(db, project_id)
        if index_md:
            parts.append("PROJECT DOCUMENT INDEX:\n" + index_md[:8000])
    except Exception:  # noqa: BLE001 — index is best-effort context
        pass
    scope = db.execute(
        _text("SELECT payload FROM scopes WHERE project_id=:p ORDER BY created_at DESC LIMIT 1"),
        {"p": project_id},
    ).scalar()
    if isinstance(scope, dict) and scope.get("categories"):
        cats = ", ".join(str(c.get("name") or "") for c in scope["categories"][:30])
        parts.append("SCOPE CATEGORIES: " + cats)
    est = db.execute(
        _text("SELECT payload FROM estimate_versions WHERE project_id=:p AND is_active=true"),
        {"p": project_id},
    ).scalar()
    if isinstance(est, dict) and est.get("categories"):
        cats = ", ".join(str(c.get("name") or "") for c in est["categories"][:30])
        parts.append("ESTIMATE CATEGORIES: " + cats)

    system = (
        "You are a construction project manager for Built By Brio (BRIO Construction). "
        "Draft the 'What We Discussed' client-discussion points for this project's client "
        "package — the notes that summarise what was reviewed with the owner (scope "
        "clarifications, options/upgrades presented, pricing caveats, schedule notes). "
        "STRICT RULES: use ONLY the facts in the provided project context; do NOT invent "
        "numbers, materials, names, dates, or scope. Write each point in plain, client-facing "
        "language. If the context is too thin to summarise, return an empty array. "
        'Reply with ONLY a JSON array of objects: [{"title": "...", "body": "..."}]. '
        "3–6 points. No markdown, no preamble."
    )
    messages = [{"role": "user", "content": "\n\n".join(parts)}]
    result = call_anthropic(MODEL_SONNET, system, messages, max_tokens=1500)

    raw = (result.text or "").strip()
    start, end = raw.find("["), raw.rfind("]")
    points: list = []
    if start != -1 and end != -1 and end > start:
        try:
            parsed = _json.loads(raw[start : end + 1])
            if isinstance(parsed, list):
                points = [
                    {"title": str(p.get("title") or "").strip(), "body": str(p.get("body") or "").strip()}
                    for p in parsed
                    if isinstance(p, dict) and (p.get("title") or p.get("body"))
                ]
        except (ValueError, TypeError):
            points = []
    return {"points": points}
