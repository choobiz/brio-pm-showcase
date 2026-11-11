"""Per-step progress on a long job.

The property that matters is VISIBILITY WHILE RUNNING. `estimate_full` holds one
session open for 375 seconds on average; progress written on that session is
invisible until it commits, which is when the job ends — reporting the past of a
thing that has finished. So every write opens its own session and commits at
once, and the test below proves it by reading through a SEPARATE connection
while a caller's transaction is still open.

⚠️ TWO THINGS THIS FILE HAD TO WORK AROUND, both worth knowing.

1. `SessionLocal` binds to DATABASE_URL, not TEST_DATABASE_URL. A service that
   opens its own session is therefore writing somewhere the fixtures do not
   control — found when these tests failed with "column steps does not exist"
   against the app's default database. The suite keeps those two apart
   deliberately; that separation is what the 2026-08-31 wipe cost. The service
   takes an injectable factory so production is unchanged and the tests land
   where they belong.

2. These tests cannot use the rollback-wrapped `db_session` fixture for setup,
   because the code under test reads through a different connection and would
   see nothing. They commit real rows and delete them afterwards.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text as _T
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal as _ORIGINAL_FACTORY  # noqa: E402
from app.services import job_progress as P  # noqa: E402


@pytest.fixture()
def pg(db_engine):
    """A committing session factory pointed at the TEST database, plus cleanup.

    Yields a small helper rather than a bare factory so every test gets the same
    create-and-clean discipline without repeating it.
    """
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    P.use_session_factory(factory)
    made: list[tuple[uuid4, uuid4]] = []

    class H:
        @staticmethod
        def job(kind: str = "estimate_full"):
            pid, jid = uuid4(), uuid4()
            with factory() as s:
                s.execute(_T("insert into projects (id, name, slug, status) "
                             "values (:i,:n,:s,'preconstruction')"),
                          {"i": str(pid), "n": "Progress", "s": f"pg-{str(pid)[:8]}"})
                s.execute(_T("insert into ai_jobs (id, project_id, kind, status) "
                             "values (:i,:p,:k,'running')"),
                          {"i": str(jid), "p": str(pid), "k": kind})
                s.commit()
            made.append((pid, jid))
            return jid

        @staticmethod
        def read(job_id):
            """Read through a connection the writer does not share."""
            with factory() as s:
                row = s.execute(_T("select steps from ai_jobs where id = :i"),
                                {"i": str(job_id)}).first()
                return list(row[0] or []) if row else []

    yield H
    with factory() as s:
        for pid, jid in made:
            s.execute(_T("delete from ai_jobs where id = :i"), {"i": str(jid)})
            s.execute(_T("delete from projects where id = :i"), {"i": str(pid)})
        s.commit()
    P.use_session_factory(_ORIGINAL_FACTORY)


# --- the property the whole module exists for -------------------------------

def test_progress_is_visible_before_the_job_finishes(pg):
    """⚠️ THE ONE THAT MATTERS. If this fails, every step line appears only when
    the job ends — exactly when nobody needs it."""
    jid = pg.job()
    P.plan(jid, ["architectural", "structural"])
    P.begin(jid, "architectural")

    steps = pg.read(jid)
    assert len(steps) == 2
    assert steps[0]["status"] == "running"
    assert steps[0]["started_at"] is not None
    assert steps[1]["status"] == "pending"


def test_steps_are_declared_up_front_so_the_denominator_is_known(pg):
    """"2 of 6" is progress. "2 so far" during a seven-minute wait is not."""
    jid = pg.job()
    P.plan(jid, ["a", "b", "c"])
    steps = pg.read(jid)
    assert [s["name"] for s in steps] == ["a", "b", "c"]
    assert all(s["status"] == "pending" for s in steps)
    assert P.summarise(steps)["total"] == 3


def test_finishing_records_the_outcome_and_a_note(pg):
    jid = pg.job()
    P.plan(jid, ["architectural"])
    P.begin(jid, "architectural")
    P.finish(jid, "architectural", note="7 categories")
    s = pg.read(jid)[0]
    assert s["status"] == "done"
    assert s["finished_at"] is not None
    assert s["note"] == "7 categories"


def test_a_failed_step_is_recorded_as_failed_not_skipped(pg):
    """One trade failing should not lose the other five — but the estimate is
    then missing a trade, and the step list is the only place that says so."""
    jid = pg.job()
    P.plan(jid, ["architectural", "structural"])
    P.finish(jid, "structural", ok=False, note="no usable output after 2 attempts")
    steps = pg.read(jid)
    bad = next(s for s in steps if s["name"] == "structural")
    assert bad["status"] == "error"
    assert P.summarise(steps)["failed"] == ["structural"]
    assert P.summarise(steps)["done"] == 0


def test_an_unplanned_step_is_appended_rather_than_dropped(pg):
    jid = pg.job()
    P.plan(jid, ["a"])
    P.begin(jid, "unexpected")
    assert [s["name"] for s in pg.read(jid)] == ["a", "unexpected"]


# --- never break the job ----------------------------------------------------

def test_progress_on_a_job_that_does_not_exist_is_silent(pg):
    """A cosmetic write must never raise into a seven-minute job."""
    P.plan(uuid4(), ["a"])
    P.begin(uuid4(), "a")
    P.finish(uuid4(), "a")


def test_a_malformed_job_id_does_not_raise(pg):
    P.begin("not-a-uuid", "a")      # swallowed and logged, by design


# --- summarising ------------------------------------------------------------

def test_summary_reports_what_is_running_now():
    out = P.summarise([
        {"name": "a", "status": "done"},
        {"name": "b", "status": "running"},
        {"name": "c", "status": "pending"},
    ])
    assert out["current"] == "b"
    assert out["done"] == 1
    assert out["percent"] == 33


def test_no_steps_gives_no_percentage_rather_than_zero():
    """0% implies a job that has not started. A job that never reports steps is
    a different thing, and the two must not look identical."""
    assert P.summarise([])["percent"] is None
    assert P.summarise(None)["percent"] is None
