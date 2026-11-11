"""Track A acceptance tests.

Covers the 3 cases the brief calls out:
  a) Missing token → 401
  b) Token with email NOT in allowlist → 401
  c) Token with allowlisted email → returns CurrentUser with role from DB
"""

from __future__ import annotations

import json
from typing import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwe

from app.auth import (
    CurrentUser,
    _DEFAULT_COOKIE_NAMES,
    _derive_key,
    get_current_user,
    require_owner,
)
from app.config import settings
from app.db import get_db


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeUser:
    def __init__(self, email: str, role: str = "viewer", id: UUID | None = None,
                 active: bool = True) -> None:
        self.id = id or uuid4()
        self.email = email
        self.role = role
        # auth.py gates on `user.active` (DB allowlist must exist AND be active).
        self.active = active


class _FakeDB:
    """Minimal stand-in for the SQLAlchemy Session — only the bits auth.py uses."""

    def __init__(self, users: list[_FakeUser] | None = None) -> None:
        self.users = users or []
        self.committed = False

    def execute(self, stmt):  # noqa: ANN001 — duck-typed
        # The only call site is `select(User).where(User.email == email_norm)`.
        # Walk the where-clause looking for a literal email string.
        target_email: str | None = None
        try:
            where = stmt.whereclause
            if where is not None:
                # BinaryExpression: left = User.email column, right = bind param
                right = getattr(where, "right", None)
                if right is not None and hasattr(right, "value"):
                    val = right.value
                    if isinstance(val, str):
                        target_email = val
        except Exception:
            target_email = None

        match = next((u for u in self.users if u.email == target_email), None)

        class _Result:
            def __init__(self, u: _FakeUser | None) -> None:
                self._u = u

            def scalar_one_or_none(self):
                return self._u

        return _Result(match)

    def add(self, obj):  # noqa: ANN001
        # Backstop user creation path
        self.users.append(_FakeUser(email=obj.email, role=getattr(obj, "role", "viewer") or "viewer"))

    def commit(self) -> None:
        self.committed = True

    def refresh(self, obj):  # noqa: ANN001
        # Find the user we just added and copy id over.
        match = next((u for u in self.users if u.email == obj.email), None)
        if match is not None:
            obj.id = match.id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


TEST_SECRET = "unit-test-secret-please-do-not-leak-1234567890abcdef"


@pytest.fixture
def app_with_user(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FastAPI, _FakeDB]]:
    """Build a tiny FastAPI app that exercises get_current_user + require_owner.

    Forces production-mode (so dev fallbacks are off) and seeds an allowlist.
    """
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(settings, "nextauth_secret", TEST_SECRET)
    monkeypatch.setattr(settings, "auth_allowed_emails_raw", "amir@sister-co.example,admin@brio.ca")

    db = _FakeDB(
        users=[
            _FakeUser(email="amir@sister-co.example", role="owner"),
            _FakeUser(email="visitor@example.com", role="viewer"),
            # require_owner is now an alias for require_superadmin (the role model
            # was refactored: 'owner' == client). The gate needs a superadmin.
            _FakeUser(email="admin@brio.ca", role="superadmin"),
        ]
    )

    app = FastAPI()

    @app.get("/whoami")
    def whoami(user: CurrentUser = Depends(get_current_user)) -> dict:
        return {"id": user.id, "email": user.email, "role": user.role}

    @app.post("/ai-jobs-test")
    def gated(user: CurrentUser = Depends(require_owner)) -> dict:
        return {"ok": True, "email": user.email}

    app.dependency_overrides[get_db] = lambda: db
    yield app, db


def _make_token(email: str, secret: str = TEST_SECRET) -> str:
    """Mint a JWE the same way Auth.js v5 does — A256CBC-HS512, salt=cookie name."""
    cookie_name = _DEFAULT_COOKIE_NAMES[0]  # "authjs.session-token"
    key = _derive_key(secret, cookie_name, 64, "Auth.js Generated Encryption Key ({salt})")
    payload = json.dumps({"email": email, "name": "Test", "sub": str(uuid4())}).encode("utf-8")
    token = jwe.encrypt(payload, key, algorithm="dir", encryption="A256CBC-HS512")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def test_missing_token_returns_401(app_with_user: tuple[FastAPI, _FakeDB]) -> None:
    app, _ = app_with_user
    client = TestClient(app)
    res = client.get("/whoami")
    assert res.status_code == 401


def test_token_with_non_allowlisted_email_returns_403(
    app_with_user: tuple[FastAPI, _FakeDB],
) -> None:
    # Valid token but email not on the DB allowlist → authenticated-but-not-
    # authorized → 403 (auth.py: "ask an administrator to invite you").
    app, _ = app_with_user
    token = _make_token("stranger@example.com")
    client = TestClient(app)
    res = client.get("/whoami", cookies={"authjs.session-token": token})
    assert res.status_code == 403


def test_token_with_allowlisted_email_returns_current_user(
    app_with_user: tuple[FastAPI, _FakeDB],
) -> None:
    app, _ = app_with_user
    token = _make_token("amir@sister-co.example")
    client = TestClient(app)
    res = client.get("/whoami", cookies={"authjs.session-token": token})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["email"] == "amir@sister-co.example"
    assert body["role"] == "owner"
    assert body["id"]


def test_require_owner_blocks_non_owner(
    app_with_user: tuple[FastAPI, _FakeDB], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _db = app_with_user
    # Add a non-owner to the allowlist for this case so the email check passes.
    monkeypatch.setattr(settings, "auth_allowed_emails_raw", "amir@sister-co.example,visitor@example.com")
    token = _make_token("visitor@example.com")
    client = TestClient(app)
    res = client.post("/ai-jobs-test", cookies={"authjs.session-token": token})
    assert res.status_code == 403


def test_require_owner_allows_superadmin(app_with_user: tuple[FastAPI, _FakeDB]) -> None:
    # require_owner == require_superadmin; a superadmin passes the internal gate.
    app, _ = app_with_user
    token = _make_token("admin@brio.ca")
    client = TestClient(app)
    res = client.post("/ai-jobs-test", cookies={"authjs.session-token": token})
    assert res.status_code == 200
    assert res.json()["email"] == "admin@brio.ca"
