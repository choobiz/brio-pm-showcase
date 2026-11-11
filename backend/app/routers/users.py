"""User management — superadmin only.

The `users` table IS the sign-in allowlist (active=true → may authenticate).
Superadmin invites people here, sets their role, activates/deactivates them,
and assigns them to projects (project_members = per-project scope for
manager/worker/owner). Sign-in itself is the NextAuth magic-link flow; a user
must be pre-provisioned here first.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_superadmin, real_user_id
from app.db import get_db
from app.models.project_member import ProjectMember
from app.models.user import ROLE_RANK, USER_ROLES, User


router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    email: str
    name: str | None = None
    role: str = "worker"
    project_ids: list[UUID] = []


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    project_ids: list[UUID] | None = None


def _project_ids(db: Session, user_id) -> list[str]:
    rows = db.execute(
        select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
    ).scalars().all()
    return [str(p) for p in rows]


def _read(db: Session, u: User) -> dict:
    return {
        "id": str(u.id), "email": u.email, "name": u.name, "role": u.role,
        "active": u.active, "created_at": u.created_at.isoformat() if u.created_at else None,
        "project_ids": _project_ids(db, u.id),
    }


def _validate_role(role: str) -> None:
    if role not in USER_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"role must be one of {', '.join(USER_ROLES)}")


def _set_members(db: Session, user_id, project_ids: list[UUID]) -> None:
    """Replace a user's project assignments with `project_ids`."""
    db.execute(text("DELETE FROM project_members WHERE user_id = :u"), {"u": user_id})
    for pid in project_ids:
        db.add(ProjectMember(project_id=pid, user_id=user_id))


@router.get("/me")
def me(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict:
    """The caller's own identity/role — used by the frontend to gate nav."""
    uid = real_user_id(user)
    u = db.get(User, uid) if uid else None
    return {
        "id": user.id, "email": user.email, "role": user.role,
        "name": u.name if u else None,
        "rank": ROLE_RANK.get(user.role, -1),
        "project_ids": _project_ids(db, UUID(user.id)) if u else [],
    }


@router.get("")
def list_users(
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_superadmin),
) -> list[dict]:
    users = db.execute(select(User).order_by(User.created_at)).scalars().all()
    return [_read(db, u) for u in users]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_superadmin),
) -> dict:
    _validate_role(body.role)
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid email address")
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="a user with that email already exists")
    u = User(email=email, name=body.name, role=body.role, active=True)
    db.add(u)
    db.flush()
    _set_members(db, u.id, body.project_ids)
    db.commit()
    db.refresh(u)
    return _read(db, u)


@router.patch("/{user_id}")
def update_user(
    user_id: UUID,
    body: UserUpdate,
    db: Session = Depends(get_db),
    actor: CurrentUser = Depends(require_superadmin),
) -> dict:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="user not found")
    # Guard: never let the last active superadmin demote/deactivate themselves out.
    if str(u.id) == actor.id or u.role == "superadmin":
        would_remove_super = (
            (body.role is not None and body.role != "superadmin" and u.role == "superadmin")
            or (body.active is False and u.role == "superadmin")
        )
        if would_remove_super:
            n_super = db.execute(
                text("SELECT count(*) FROM users WHERE role='superadmin' AND active=true")
            ).scalar()
            if n_super is not None and n_super <= 1:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    detail="cannot remove the last active superadmin")
    if body.role is not None:
        _validate_role(body.role)
        u.role = body.role
    if body.name is not None:
        u.name = body.name
    if body.active is not None:
        u.active = body.active
    if body.project_ids is not None:
        _set_members(db, u.id, body.project_ids)
    db.commit()
    db.refresh(u)
    return _read(db, u)


@router.delete("/{user_id}")
def deactivate_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: CurrentUser = Depends(require_superadmin),
) -> dict:
    """Soft-delete: deactivate (users have FK references, so we never hard-delete)."""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="user not found")
    if u.role == "superadmin":
        n_super = db.execute(
            text("SELECT count(*) FROM users WHERE role='superadmin' AND active=true")
        ).scalar()
        if n_super is not None and n_super <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="cannot deactivate the last active superadmin")
    u.active = False
    db.commit()
    return {"ok": True, "id": str(u.id), "active": u.active}
