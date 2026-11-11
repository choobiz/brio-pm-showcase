"""Track C — scope_analysis worker happy path.

Mocks the Anthropic call to return a fixed JSON scope, then exercises the
real worker code path (claim_one_job → run_job). Asserts that:
  - a Scope row exists with version 1 + the parsed payload
  - the AIJob ends in `status='done'` with `output.scope_id` matching
  - token / cost columns get populated

The integration test requires Postgres (Scope.payload is JSONB). It
auto-skips when the test DB is not reachable via the existing `db_session`
fixture. A pure-unit test exercises the prompt builder without a DB.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest

from app.models.ai_job import AIJob
from app.models.client import Client
from app.models.project import Project
from app.models.scope import Scope
from app.services.ai import (
    AnthropicResult,
    build_messages_for_job,
    build_scope_analysis_messages,
)
from app.workers import ai_worker
from app.workers.ai_worker import claim_one_job, run_job


# ---------------------------------------------------------------------------
# Pure prompt-builder tests (no Postgres required)
# ---------------------------------------------------------------------------


def test_build_scope_analysis_messages_no_images():
    system, messages, max_tokens = build_scope_analysis_messages({
        "notes": "Client wants full gut.",
        "project_metadata": {
            "address": "123 Test Ln",
            "building_age": 1985,
            "project_type": "Bathroom Renovation",
        },
        "images": [],
    })
    assert "Brio" in system
    assert "JSON" in system
    assert max_tokens == 16000
    assert len(messages) == 1
    msg = messages[0]
    assert msg["role"] == "user"
    content = msg["content"]
    assert isinstance(content, list)
    # One text block, no image blocks.
    assert content[0]["type"] == "text"
    assert "123 Test Ln" in content[0]["text"]
    assert "Bathroom Renovation" in content[0]["text"]
    assert "Client wants full gut" in content[0]["text"]
    assert all(b.get("type") != "image" for b in content)


def test_build_scope_analysis_messages_with_image_and_caption():
    _, messages, _ = build_scope_analysis_messages({
        "notes": "",
        "images": [
            {"media_type": "image/png", "data": "AAA=", "caption": "tub corner damage"},
        ],
    })
    content = messages[0]["content"]
    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/png"
    assert image_blocks[0]["source"]["data"] == "AAA="
    # Caption should appear as a text block ahead of the image.
    caption_idx = next(
        i for i, b in enumerate(content)
        if b.get("type") == "text" and "tub corner damage" in b.get("text", "")
    )
    image_idx = next(i for i, b in enumerate(content) if b.get("type") == "image")
    assert caption_idx < image_idx


def test_build_messages_for_job_routes_scope_analysis():
    system, messages, max_tokens = build_messages_for_job(
        "scope_analysis",
        {"notes": "x", "images": []},
    )
    assert max_tokens == 16000
    assert "construction scope analyst" in (system or "")
    assert messages[0]["role"] == "user"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_VALID_SCOPE_JSON = json.dumps({
    "reference": "SCOPE-1",
    "date": "2026-05-12",
    "categories": [{"name": "Demolition & Removal", "items": ["Remove existing tile"]}],
    "materials": [],
    "potentialIssues": [],
    "accessConstraints": [],
    "permits": [],
    "assumptions": [],
    "exclusions": [],
})


def _seed_project(db_session) -> Project:
    client = Client(name="Test Client")
    db_session.add(client)
    db_session.flush()
    project = Project(
        slug=f"scope-test-{uuid.uuid4().hex[:8]}",
        name="Scope Test Bathroom",
        address="123 Test Lane, North Vancouver BC",
        type="Bathroom Renovation",
        building_age=1985,
        building_type="house",
        sqft=80,
        client_id=client.id,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _stub_result(text: str = _VALID_SCOPE_JSON) -> AnthropicResult:
    return AnthropicResult(
        text=text,
        input_tokens=100,
        output_tokens=200,
        cost_usd=Decimal("0.0234"),
        model="claude-opus-5-5",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_scope_analysis_worker_persists_scope_row(monkeypatch, worker_db):
    # Seed durably so the worker's separate connections can see the job.
    with worker_db() as db:
        project = _seed_project(db)
        project_id = project.id
        job = AIJob(
            project_id=project_id,
            kind="scope_analysis",
            status="queued",
            input={
                "images": [],
                "notes": "Client wants a full gut renovation. Visible mould near the tub.",
            },
        )
        db.add(job)
        db.commit()
        job_id = job.id

    # Stub the Anthropic call and point the worker's `SessionLocal` at the
    # same (real, committing) sessionmaker the fixture provides.
    monkeypatch.setattr(
        ai_worker,
        "_call_with_retry",
        lambda **_kwargs: _stub_result(),
    )
    monkeypatch.setattr(ai_worker, "SessionLocal", worker_db)

    # Run claim + execute
    with worker_db() as wdb:
        claimed = claim_one_job(wdb)
        assert claimed is not None
        assert claimed.id == job_id
        assert claimed.kind == "scope_analysis"
        assert claimed.model == "claude-opus-5-5"

    run_job(job_id)

    with worker_db() as vdb:
        final_job = vdb.get(AIJob, job_id)
        assert final_job is not None
        assert final_job.status == "done", f"job error: {final_job.error}"
        assert final_job.output is not None
        scope_id = final_job.output.get("scope_id")
        assert scope_id, "expected scope_id in AIJob.output"
        assert final_job.input_tokens == 100
        assert final_job.output_tokens == 200

        scope = vdb.get(Scope, uuid.UUID(scope_id))
        assert scope is not None
        assert scope.project_id == project_id
        assert scope.version == 1
        assert scope.payload["reference"] == "SCOPE-1"
        assert scope.payload["categories"][0]["name"] == "Demolition & Removal"
    # `worker_db` wipes all tables on teardown — no manual cleanup needed.


def test_scope_analysis_worker_marks_error_on_bad_json(monkeypatch, worker_db):
    with worker_db() as db:
        project = _seed_project(db)
        project_id = project.id
        job = AIJob(
            project_id=project_id,
            kind="scope_analysis",
            status="queued",
            input={"images": [], "notes": ""},
        )
        db.add(job)
        db.commit()
        job_id = job.id

    monkeypatch.setattr(
        ai_worker,
        "_call_with_retry",
        lambda **_kwargs: _stub_result(text="this is not JSON, sorry"),
    )
    monkeypatch.setattr(ai_worker, "SessionLocal", worker_db)

    with worker_db() as wdb:
        claimed = claim_one_job(wdb)
        assert claimed is not None
        assert claimed.id == job_id

    run_job(job_id)

    with worker_db() as vdb:
        final_job = vdb.get(AIJob, job_id)
        assert final_job is not None
        assert final_job.status == "error"
        assert final_job.error and "JSON parse failure" in final_job.error
        # No scope row should exist.
        from sqlalchemy import select as _select

        rows = vdb.execute(_select(Scope).where(Scope.project_id == project_id)).scalars().all()
        assert rows == []


def test_scope_date_is_the_day_it_was_written_not_the_models(db_session):
    """Rollout test 2026-10-14: a scope written that day was headed 2025-01-16,
    a date the model invented. The saved date must be today (Vancouver)."""
    from types import SimpleNamespace

    from app.services import scope_derive
    from app.services.home import today
    from app.workers.ai_worker import _persist_scope_from_result

    project = _seed_project(db_session)
    body = json.loads(_VALID_SCOPE_JSON)
    body["date"] = "2025-01-16"
    job = SimpleNamespace(project_id=project.id, id=None, input={})
    scope, err = _persist_scope_from_result(db_session, job, _stub_result(json.dumps(body)))
    assert err is None
    assert scope.payload["date"] == today().isoformat()
    assert scope_derive.stamp({"date": "2025-01-16"})["date"] == today().isoformat()
