"""NextAuth v5 (Auth.js) session-token verification + DB user lookup.

NextAuth v5 issues encrypted JWE session tokens. The default encoder:
  - JWE alg = "dir" (direct encryption — symmetric key is the derived key)
  - JWE enc = "A256CBC-HS512"  (current stable v5; older betas used "A256GCM")
  - Encryption key is derived from `NEXTAUTH_SECRET` via HKDF-SHA256:
      info  = utf8("Auth.js Generated Encryption Key (<salt>)")
      salt  = the cookie name, typically:
                "authjs.session-token"           (http / dev)
                "__Secure-authjs.session-token"  (https / prod)
      length = 64 bytes for A256CBC-HS512, 32 bytes for A256GCM
  - Earlier "next-auth" v4 used info = "NextAuth.js Generated Encryption Key"
    and salt = "" (HKDF salt), key length 32. We attempt that as a fallback
    for completeness, but v5 is the primary path.

Reference: https://authjs.dev/concepts/session-strategies
           https://github.com/nextauthjs/next-auth (packages/core/src/jwt.ts)

We try a small set of (info, salt, length, enc) tuples derived from
`NEXTAUTH_SECRET` and pick the first one that decrypts.

Dev fallback: if `NEXTAUTH_SECRET` is unset (== "change-me") OR the request
carries the legacy `X-User-Email` header AND `ENV != "production"`, we honor
the W1 stub behavior so other tracks can keep iterating without real auth.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Iterable, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Cookie, Depends, Header, HTTPException, status
from jose import jwe  # type: ignore[import-untyped]
from jose.exceptions import JOSEError  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.user import ROLE_RANK, User


# ---------------------------------------------------------------------------
# Public dataclass — keep shape stable; other routers import this.
# ---------------------------------------------------------------------------


@dataclass
class CurrentUser:
    id: str
    email: str
    role: str = "viewer"


DEV_USER_ID = "00000000-0000-0000-0000-000000000000"


def real_user_id(user: CurrentUser) -> "UUID | None":
    """The user's id for created_by / decided_by columns, or None for the
    dev-fallback user (all zeros), which has no users row.

    Routes used `user.id[0] != "0"`, which also dropped every REAL id that
    happens to start with 0 (1 in 16): their actions were recorded with no
    author, and the office-notification query then failed on the NULL
    (found by a flaky CI run, 2026-11-02)."""
    from uuid import UUID

    if not user.id or str(user.id) == DEV_USER_ID:
        return None
    try:
        return UUID(str(user.id))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Key derivation + JWE decode
# ---------------------------------------------------------------------------


# Cookie names NextAuth v5 uses for the encrypted session JWT.
_DEFAULT_COOKIE_NAMES: tuple[str, ...] = (
    "authjs.session-token",
    "__Secure-authjs.session-token",
    # v4 legacy names — kept for graceful compatibility:
    "next-auth.session-token",
    "__Secure-next-auth.session-token",
)


def _derive_key(secret: str, salt: str, length: int, info_template: str) -> bytes:
    info = info_template.format(salt=salt).encode("utf-8")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt.encode("utf-8"),
        info=info,
    ).derive(secret.encode("utf-8"))


def _candidate_keys(secret: str) -> Iterable[bytes]:
    """Yield candidate keys to try, in order of likelihood for v5 → v4.

    We don't need to pass `enc` to `jwe.decrypt` — python-jose reads it from
    the JWE header. We only vary key length + derivation parameters.
    """
    for cookie_name in _DEFAULT_COOKIE_NAMES:
        # v5 default — A256CBC-HS512 needs 64-byte key
        yield _derive_key(secret, cookie_name, 64, "Auth.js Generated Encryption Key ({salt})")
        # v5 older betas — A256GCM needs 32-byte key, same info template
        yield _derive_key(secret, cookie_name, 32, "Auth.js Generated Encryption Key ({salt})")
    # v4 legacy — A256GCM, 32-byte key, no salt, fixed info string
    yield _derive_key(secret, "", 32, "NextAuth.js Generated Encryption Key")


def _decode_session_token(token: str, secret: str) -> Optional[dict]:
    """Try each candidate key; return the JSON payload of the first that
    successfully decrypts. Returns None if none work."""
    for key in _candidate_keys(secret):
        try:
            payload = jwe.decrypt(token, key)
            if payload is None:
                continue
            # python-jose returns bytes
            data = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
            if isinstance(data, dict):
                return data
        except (JOSEError, ValueError, json.JSONDecodeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_production() -> bool:
    return (os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or "").lower() == "production"


def _is_secret_unset() -> bool:
    s = settings.nextauth_secret
    return not s or s == "change-me" or s == "change-me-and-match-frontend"


def _allowlist() -> set[str]:
    return {e.strip().lower() for e in settings.auth_allowed_emails if e.strip()}


def _resolve_user_in_db(db: Session, email: str) -> Optional[User]:
    """Look up a user by email. Returns None if not provisioned — we no longer
    auto-create (the DB `users` table IS the allowlist; superadmin invites)."""
    email_norm = email.strip().lower()
    return db.execute(select(User).where(User.email == email_norm)).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


def get_current_user(
    db: Session = Depends(get_db),
    x_user_email: Optional[str] = Header(default=None, alias="X-User-Email"),
    authjs_session: Optional[str] = Cookie(default=None, alias="authjs.session-token"),
    authjs_session_secure: Optional[str] = Cookie(
        default=None, alias="__Secure-authjs.session-token"
    ),
    nextauth_session: Optional[str] = Cookie(default=None, alias="next-auth.session-token"),
    nextauth_session_secure: Optional[str] = Cookie(
        default=None, alias="__Secure-next-auth.session-token"
    ),
    authorization: Optional[str] = Header(default=None),
) -> CurrentUser:
    """Resolve the current user from a NextAuth v5 JWE session cookie.

    Order of resolution:
      1. Real JWE decryption (cookie or `Authorization: Bearer <jwe>`)
      2. Dev fallback: X-User-Email header (only if ENV != "production"
         AND NEXTAUTH_SECRET is unset OR allowlist accepts it)
      3. Otherwise → 401
    """
    # --- Try a JWE token from cookie or Authorization header ---------------
    token: Optional[str] = (
        authjs_session
        or authjs_session_secure
        or nextauth_session
        or nextauth_session_secure
    )
    if token is None and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()

    secret = settings.nextauth_secret
    if token and not _is_secret_unset():
        payload = _decode_session_token(token, secret)
        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid session token",
            )
        email = (payload.get("email") or "").strip().lower()
        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="session token missing email",
            )
        # DB-driven allowlist: the user must exist AND be active to authenticate.
        user = _resolve_user_in_db(db, email)
        if user is None or not user.active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="not authorized — ask an administrator to invite you",
            )
        return CurrentUser(id=str(user.id), email=user.email, role=user.role)

    # --- Dev fallback: X-User-Email header ---------------------------------
    if x_user_email and not _is_production() and _is_secret_unset():
        email = x_user_email.strip().lower()
        user = _resolve_user_in_db(db, email)
        if user is not None:
            return CurrentUser(id=str(user.id), email=user.email, role=user.role)
        return CurrentUser(id=DEV_USER_ID, email=email, role="superadmin")

    # --- Fully anonymous dev fallback (legacy stub) ------------------------
    if not _is_production() and _is_secret_unset():
        return CurrentUser(
            id=DEV_USER_ID,
            email="dev@brio.local",
            role="superadmin",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="not authenticated",
    )


def require_role(*allowed: str):
    """Generic role gate — user's role must be one of `allowed`."""

    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return user

    return _dep


def _min_rank_gate(min_role: str):
    """Hierarchical gate — caller's role rank must be >= `min_role`'s rank.
    `owner` (client, rank 0) never passes an internal gate."""
    threshold = ROLE_RANK[min_role]

    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if ROLE_RANK.get(user.role, -1) < threshold:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=f"requires {min_role} or higher",
            )
        return user

    return _dep


# Field surfaces (punch clock, daily reports): worker/manager/superadmin.
require_field = _min_rank_gate("worker")
# Office surfaces (projects, estimates, contracts, reports review): manager/superadmin.
require_office = _min_rank_gate("manager")


def require_superadmin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Top-level gate — user management, AI actions, global admin."""
    if user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="superadmin required")
    return user


# Back-compat alias: the old AI-action gate was `require_owner` when 'owner'
# meant BRIO ownership. That role is now 'superadmin' ('owner' = the client).
require_owner = require_superadmin


def assert_project_access(db: Session, user: CurrentUser, project_id) -> None:
    """Authorize `user` for a specific project.

    Staff model: `superadmin` has global access; every other role (manager /
    worker / owner-client) must be an explicit `ProjectMember` of the project.
    Raises 404 if the project doesn't exist, 403 if the user isn't allowed.
    """
    from uuid import UUID as _UUID

    from app.models.project import Project
    from app.models.project_member import ProjectMember

    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    if user.role == "superadmin":
        return
    try:
        uid = _UUID(str(user.id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="no access to this project")
    if db.get(ProjectMember, (project_id, uid)) is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="no access to this project")


def require_project_member(
    project_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Router-level guard for a router whose PREFIX carries `{project_id}`.

    Declared once as `APIRouter(..., dependencies=[Depends(require_project_member)])`
    so every route under the prefix is checked, including ones added later.
    Per-route `assert_project_access` calls are easy to forget; the workbench
    router had 45 routes and none of them checked (found 2026-10-12).
    """
    from uuid import UUID as _UUID

    try:
        pid = _UUID(str(project_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    assert_project_access(db, user, pid)
    return user


def accessible_project_ids(db: Session, user: CurrentUser) -> list:
    """Every project id this user may see — ALWAYS an explicit list.

    Used by the cross-project surfaces (document search, the register) where
    there is no single project_id to check against.

    Deliberately not "None means all". A sentinel that callers must remember to
    special-case is how one forgotten branch turns into a cross-project leak,
    and project isolation is the one property here with no acceptable failure
    rate. Materialising the ids costs one indexed read against a table holding
    tens of rows; if that ever stops being true, add a guarded fast path rather
    than reintroducing the sentinel.
    """
    from uuid import UUID as _UUID

    from app.models.project import Project
    from app.models.project_member import ProjectMember

    if user.role == "superadmin":
        return [r[0] for r in db.execute(select(Project.id)).all()]
    try:
        uid = _UUID(str(user.id))
    except (ValueError, TypeError):
        return []
    return [
        r[0] for r in db.execute(
            select(ProjectMember.project_id).where(ProjectMember.user_id == uid)
        ).all()
    ]


__all__ = [
    "CurrentUser", "get_current_user", "require_role",
    "require_field", "require_office", "require_superadmin", "require_owner",
    "assert_project_access", "accessible_project_ids", "require_project_member",
]
