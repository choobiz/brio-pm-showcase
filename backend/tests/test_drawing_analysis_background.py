"""Drawing analysis never runs inside a web request.

2026-10-26, 456 W Sample: GET /drawings/{id}/analysis parsed the corridor
drawing set in the request (15-20 s, ~600 MB). uvicorn's supervisor killed the
API worker handling it twice, the measure page showed "API 502", and nothing
was ever stored, so every visit started the same parse again.

Pinned here:
  1. The endpoint never parses. Nothing stored -> one job queued, 202 "analysing".
  2. A second request while the job is pending queues nothing new.
  3. A stored, current analysis is returned as before (200).
  4. A failed parse says so; asking again with force queues a new job.
  5. The worker stores the result and marks the job done, or error on failure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Response

from app.models.ai_job import AI_JOB_KINDS
from app.routers import drawings as R
from app.services.drawing import analysis_queue as Q
from app.services.drawing import cache as C

DOC = uuid4()
PROJECT = uuid4()


class FakeDB:
    def __init__(self, doc):
        self.doc, self.commits = doc, 0

    def get(self, model, key):
        return self.doc

    def commit(self):
        self.commits += 1


@pytest.fixture
def env(monkeypatch):
    doc = SimpleNamespace(id=DOC, project_id=PROJECT, filename="A-101.pdf", mime="application/pdf",
                          category="drawing", r2_key="k")
    state = {"job": None, "cached": None, "queued": []}

    def boom(*a, **k):
        raise AssertionError("the drawing was parsed inside the web request")

    monkeypatch.setattr(C, "analyze_drawing", boom)
    monkeypatch.setattr(C, "get_or_analyze", boom)
    monkeypatch.setattr(R, "assert_project_access", lambda *a, **k: None)
    monkeypatch.setattr(R, "latest_job", lambda db, d: state["job"])
    monkeypatch.setattr(R, "cached_analysis", lambda db, d: state["cached"])

    def queue(db, d, force=False):
        job = SimpleNamespace(id=uuid4(), status="queued", force=force)
        state["queued"].append(job)
        state["job"] = job
        return job

    monkeypatch.setattr(R, "queue_analysis", queue)
    return state, FakeDB(doc)


def _get(db, force=False):
    resp = Response()
    body = R.get_drawing_analysis(DOC, resp, force=force, db=db, user=None)
    return resp.status_code, body


def test_nothing_stored_queues_one_job_and_answers_at_once(env):
    state, db = env
    code, body = _get(db)
    assert code == 202 and body["analysis_status"] == "analysing"
    assert len(state["queued"]) == 1 and db.commits == 1


def test_a_second_request_while_pending_queues_nothing_new(env):
    state, db = env
    _get(db)
    code, body = _get(db)
    assert code == 202 and body["analysis_status"] == "analysing"
    assert len(state["queued"]) == 1
    state["job"].status = "running"
    _get(db, force=True)
    assert len(state["queued"]) == 1, "force while a parse is running must not start a second one"


def test_a_stored_analysis_is_returned(env):
    state, db = env
    now = datetime.now(timezone.utc)
    state["cached"] = SimpleNamespace(id=uuid4(), document_id=DOC, content_hash="h", is_vector=True, page_count=4,
                                      created_at=now, updated_at=now, payload={"pages": [], "candidates": []})
    code, body = _get(db)
    assert code == 200 and body["page_count"] == 4 and "analysis_status" not in body
    assert state["queued"] == []


def test_a_failed_parse_says_so_and_force_queues_again(env):
    state, db = env
    state["job"] = SimpleNamespace(id=uuid4(), status="error", error="drawing_analysis: bad pdf")
    code, body = _get(db)
    assert code == 202 and body["analysis_status"] == "failed" and "bad pdf" in body["error"]
    assert state["queued"] == [], "a failure must not silently retry on every poll"
    code, body = _get(db, force=True)
    assert body["analysis_status"] == "analysing" and len(state["queued"]) == 1 and state["queued"][0].force


def test_the_kind_exists_and_only_drawing_pdfs_are_queued_on_upload():
    assert "drawing_analysis" in AI_JOB_KINDS
    pdf = SimpleNamespace(filename="Corridor.pdf", mime="application/pdf", category="drawing")
    assert Q.is_drawing_pdf(pdf)
    assert not Q.is_drawing_pdf(SimpleNamespace(filename="Spec.pdf", mime="application/pdf", category="other"))
    assert not Q.is_drawing_pdf(SimpleNamespace(filename="Plan.dwg", mime="image/vnd.dwg", category="drawing"))


class _WorkerDB:
    def __init__(self, job):
        self.job, self.commits, self.rollbacks = job, 0, 0

    def get(self, model, key):
        return self.job

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_the_worker_stores_the_result_and_finishes_the_job(monkeypatch):
    from app.workers import ai_worker as w

    job = SimpleNamespace(id=uuid4(), status="running", output=None, error=None, finished_at=None)
    seen = {}

    def fake(db, doc_id, force=False):
        seen["args"] = (doc_id, force)
        return SimpleNamespace(id=uuid4(), page_count=4, is_vector=True)

    monkeypatch.setattr(C, "get_or_analyze", fake)
    db = _WorkerDB(job)
    w._run_drawing_analysis(db, job, {"document_id": str(DOC), "force": True})
    assert job.status == "done" and job.output["page_count"] == 4
    assert seen["args"] == (DOC, True)


def test_a_worker_failure_marks_the_job_error(monkeypatch):
    from app.workers import ai_worker as w

    job = SimpleNamespace(id=uuid4(), status="running", output=None, error=None, finished_at=None)

    def fail(*a, **k):
        raise RuntimeError("cannot open broken document")

    monkeypatch.setattr(C, "get_or_analyze", fail)
    db = _WorkerDB(job)
    w._run_drawing_analysis(db, job, {"document_id": str(DOC)})
    assert job.status == "error" and "cannot open broken document" in job.error and db.rollbacks == 1


def test_queue_analysis_reuses_a_pending_job(db_session):
    """Two requests at once must not queue two parses of the same drawing."""
    from sqlalchemy import text

    pid = uuid4()
    db_session.execute(text("insert into projects (id, name, slug, status) values (:i, 'DA', :s, 'lead')"),
                       {"i": str(pid), "s": f"da-{pid.hex[:8]}"})
    doc = SimpleNamespace(id=uuid4(), project_id=pid)
    first = Q.queue_analysis(db_session, doc)
    second = Q.queue_analysis(db_session, doc)
    assert first.id == second.id and first.kind == "drawing_analysis"
    assert Q.latest_job(db_session, doc.id).id == first.id
