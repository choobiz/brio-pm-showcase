"""Test configuration shared across the backend test suite.

Adds the `backend/` directory to sys.path so `import app...` works when pytest
is invoked from the repo root, and exposes Postgres-backed fixtures for tests
that need real ARRAY columns / FK behavior.

Tests that don't touch the DB (e.g. pure logic tests) just import normally.
DB-backed tests request the `db_session` fixture; if Postgres isn't reachable,
those tests skip with a clear message rather than failing.

The DB fixtures here are DESTRUCTIVE — ``worker_db`` deletes every row from the
domain tables and commits, before and after each test. They therefore refuse to
run against anything that is not provably a throwaway database.

``TEST_DATABASE_URL`` must be set explicitly. There is deliberately no fallback
to ``DATABASE_URL``: on 2026-08-31 this suite was run inside the production API
container to get a Python 3.12 interpreter, ``TEST_DATABASE_URL`` was unset,
``DATABASE_URL`` pointed at production, and the wipe destroyed 341 projects and
~5,300 document rows. A destructive fixture that quietly falls back to whatever
connection string happens to be in the environment is a loaded gun.

Three independent guards, because any one of them can be got around by accident:

  1. ``TEST_DATABASE_URL`` must be set — no inheriting a connection string.
  2. Its database name must look like a test database.
  3. The target must be EMPTY of project data before anything is wiped. This is
     the backstop that still holds when someone points TEST_DATABASE_URL at the
     wrong host, and it is the one that would have prevented the incident.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


#: A database name must match one of these to be wiped. Anything else is assumed
#: to be somebody's real data.
_TEST_DB_NAMES = ("test", "_test", "brio_pm_test", "throwaway", "scratch")

#: More project rows than this and the target is not a throwaway. A fixture-built
#: project or two is expected; a portfolio is not.
_MAX_PROJECTS_IN_A_TEST_DB = 5


class UnsafeTestDatabase(RuntimeError):
    """Raised instead of destroying data that was not ours to destroy."""


MISSING_URL_MESSAGE = (
    "TEST_DATABASE_URL is not set. These fixtures DELETE every row from the "
    "domain tables, so they will not fall back to DATABASE_URL — that fallback is "
    "how production was wiped on 2026-08-31. Set TEST_DATABASE_URL to a throwaway "
    "database, e.g. postgresql+psycopg://brio:brio@localhost:5434/brio_pm_test")


def pytest_configure(config) -> None:
    """Refuse to run a suite that would silently skip its database tests.

    The guard below makes an absent TEST_DATABASE_URL a SKIP, which is right on a
    developer machine with no throwaway database. In CI it is a trap: the run
    goes green while every DB-backed test is skipped, and nothing says so louder
    than a count in the summary line.

    That is not hypothetical. CI set DATABASE_URL and never TEST_DATABASE_URL, so
    from the day this guard landed (after the 2026-08-31 production wipe) until
    2026-10-02, **141 database tests were skipped on every run** and the build
    still reported success. Setting REQUIRE_DB_TESTS makes the omission an error
    instead of a silence.
    """
    if os.environ.get("REQUIRE_DB_TESTS") and not os.environ.get("TEST_DATABASE_URL"):
        raise pytest.UsageError(
            "REQUIRE_DB_TESTS is set but TEST_DATABASE_URL is not, so every "
            "database-backed test would be skipped and the run would still pass. "
            "Point TEST_DATABASE_URL at a throwaway database whose name contains "
            "'test', or unset REQUIRE_DB_TESTS if you genuinely want the skips."
        )


def _resolve_url() -> str | None:
    """The throwaway database, or None. Never an inherited connection string.

    Absent is a SKIP (the caller has no test database, so no DB test can run) but
    present-and-wrong is a hard ERROR — a wrong target is the dangerous case and
    it must never pass quietly.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return None
    name = url.rsplit("/", 1)[-1].split("?")[0].lower()
    if not any(k in name for k in _TEST_DB_NAMES):
        raise UnsafeTestDatabase(
            f"TEST_DATABASE_URL points at a database called {name!r}, which does "
            f"not look like a throwaway. Expected the name to contain one of "
            f"{_TEST_DB_NAMES}. Refusing to wipe it.")
    return url


def _assert_safe_to_wipe(engine) -> None:
    """Refuse to wipe a database that holds real project data.

    The last line of defence, and the only guard that survives someone setting
    TEST_DATABASE_URL to the wrong host. It reads before it writes.
    """
    from sqlalchemy import text as _t

    with engine.connect() as conn:
        exists = conn.execute(_t(
            "select to_regclass('public.projects') is not null")).scalar()
        if not exists:
            return                     # fresh schema, nothing to protect
        n = conn.execute(_t("select count(*) from projects")).scalar() or 0
    if n > _MAX_PROJECTS_IN_A_TEST_DB:
        raise UnsafeTestDatabase(
            f"Refusing to wipe: the target database holds {n} projects. A "
            f"throwaway test database should hold at most "
            f"{_MAX_PROJECTS_IN_A_TEST_DB}. Check TEST_DATABASE_URL — it is "
            f"pointing at real data.")


@pytest.fixture(scope="session")
def db_engine():
    url = _resolve_url()
    if url is None:
        pytest.skip(MISSING_URL_MESSAGE)
    try:
        engine = create_engine(url, future=True)
        # eager connect so we fail fast with a clear skip when Postgres is down
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable at {url}: {exc}")

    # Lazy import so the skip path doesn't import models that touch settings.
    from app.db import Base
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _assert_safe_to_wipe(engine)
    yield engine


@pytest.fixture()
def db_session(db_engine):
    """Per-test session wrapped in a transaction so each test rolls back."""

    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        from app.models.client import Client
        from app.models.payment import Payment
        from app.models.invoice import Invoice
        from app.models.estimate import Estimate
        from app.models.scope import Scope
        from app.models.document import Document
        from app.models.activity_log import ActivityLog
        from app.models.project_member import ProjectMember
        from app.models.ai_job import AIJob
        from app.models.project import Project
        from app.models.user import User

        for model in (
            Payment,
            Invoice,
            Estimate,
            Scope,
            Document,
            ActivityLog,
            ProjectMember,
            AIJob,
            Project,
            Client,
        ):
            session.execute(model.__table__.delete())
        session.flush()
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def worker_db(db_engine):
    """A sessionmaker on the real engine for tests that span multiple
    connections.

    The rollback-wrapped ``db_session`` fixture keeps every write inside one
    connection's transaction, so data is invisible to any other connection and
    is discarded at test end. That is fine for single-connection tests, but the
    AI worker opens its *own* short Sessions via ``SessionLocal()`` — it can
    only see data that is durably committed.

    This fixture hands back a plain ``sessionmaker`` whose commits are real and
    visible across connections. All domain tables are wiped (committed) before
    and after the test so each run starts from a clean slate.
    """

    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)

    from app.models.client import Client
    from app.models.payment import Payment
    from app.models.invoice import Invoice
    from app.models.estimate import Estimate
    from app.models.scope import Scope
    from app.models.document import Document
    from app.models.activity_log import ActivityLog
    from app.models.project_member import ProjectMember
    from app.models.ai_job import AIJob
    from app.models.project import Project

    ordered_models = (
        Payment,
        Invoice,
        Estimate,
        Scope,
        Document,
        ActivityLog,
        ProjectMember,
        AIJob,
        Project,
        Client,
    )

    def _wipe() -> None:
        _assert_safe_to_wipe(db_engine)      # re-checked every time, not once
        with Session() as s:
            for model in ordered_models:
                s.execute(model.__table__.delete())
            s.commit()

    _wipe()
    try:
        yield Session
    finally:
        _wipe()
