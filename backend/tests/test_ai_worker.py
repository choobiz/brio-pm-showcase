"""Tests for Track F — AI worker / job queue.

Coverage:
  a) Cost computation — pure function, no DB / network.
  b) Job claim with FOR UPDATE SKIP LOCKED — two simulated workers must not
     both grab the same row. Requires Postgres; auto-skips on other dialects.
  c) Retry on 429 — mocked Anthropic client raises RateLimitError twice then
     succeeds. Worker should call 3 times total and end status='done'.
  d) No retry on validation (400) error — mocked Anthropic raises BadRequestError
     once; worker should call exactly once and mark status='error'.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import anthropic
import httpx
import pytest

from app.services.ai import (
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
    compute_cost_usd,
)


# ---------------------------------------------------------------------------
# (a) Cost computation
# ---------------------------------------------------------------------------


def test_cost_sonnet_1k_in_500_out():
    """Sonnet 5.5: 1000 input @ $2/M + 500 output @ $10/M = $0.0070."""
    cost = compute_cost_usd(MODEL_SONNET, input_tokens=1000, output_tokens=500)
    expected = Decimal("0.0070")
    # 1% tolerance per acceptance criterion.
    assert abs(cost - expected) <= expected * Decimal("0.01"), (
        f"expected ≈ {expected}, got {cost}"
    )


def test_cost_opus_zero_tokens_is_zero():
    assert compute_cost_usd(MODEL_OPUS, 0, 0) == Decimal("0.0000")


def test_cost_haiku_one_million_each():
    # 1M input @ $1 + 1M output @ $5 = $6.00 exactly.
    cost = compute_cost_usd(MODEL_HAIKU, 1_000_000, 1_000_000)
    assert cost == Decimal("6.0000")


def test_cost_opus_matches_rate_card():
    # Opus 5.5: 10k in @ $4/M = $0.04 ; 5k out @ $20/M = $0.10 ; total $0.14.
    cost = compute_cost_usd(MODEL_OPUS, 10_000, 5_000)
    assert cost == Decimal("0.1400")
    # A cost recorded under the old model still prices at the old rates.
    assert compute_cost_usd("claude-opus-4-7", 10_000, 5_000) == Decimal("0.5250")


def test_the_writers_use_the_5_5_models():
    """Amir approved the move on 2026-10-15; Ask and Prepare were already on 5.5."""
    from app.services.ai import model_for_kind

    assert model_for_kind("scope_analysis") == "claude-opus-5-5"
    assert model_for_kind("ai_edit") == "claude-opus-5-5"
    assert model_for_kind("estimate_generation") == "claude-sonnet-5-5"


# ---------------------------------------------------------------------------
# Helpers for retry tests — fake Anthropic client
# ---------------------------------------------------------------------------


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeResponse:
    content: list[_FakeBlock]
    usage: _FakeUsage


def _success_response(text: str = "ok") -> _FakeResponse:
    return _FakeResponse(
        content=[_FakeBlock(text=text)],
        usage=_FakeUsage(input_tokens=10, output_tokens=5),
    )


def _make_rate_limit_error() -> anthropic.RateLimitError:
    """Build a real anthropic.RateLimitError (HTTP 429)."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(429, request=request)
    return anthropic.RateLimitError(
        message="rate limited (test)",
        response=response,
        body={"error": {"type": "rate_limit_error", "message": "test"}},
    )


def _make_bad_request_error() -> anthropic.BadRequestError:
    """Build a real anthropic.BadRequestError (HTTP 400)."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    return anthropic.BadRequestError(
        message="invalid request (test)",
        response=response,
        body={"error": {"type": "invalid_request_error", "message": "test"}},
    )


def _build_fake_client(side_effects: list[Any]) -> MagicMock:
    """Return a fake `Anthropic`-shaped client whose .messages.create
    consumes `side_effects` left-to-right (raise if exception, return otherwise)."""
    client = MagicMock()
    iterator = iter(side_effects)

    def _create(**_kwargs: Any) -> _FakeResponse:
        try:
            value = next(iterator)
        except StopIteration as exc:  # pragma: no cover
            raise AssertionError("client.messages.create called more times than expected") from exc
        if isinstance(value, BaseException):
            raise value
        return value

    client.messages.create.side_effect = _create
    return client


# ---------------------------------------------------------------------------
# (c) Retry on 429
# ---------------------------------------------------------------------------


def test_call_with_retry_succeeds_after_two_429s():
    from app.workers.ai_worker import _call_with_retry

    fake = _build_fake_client(
        [
            _make_rate_limit_error(),
            _make_rate_limit_error(),
            _success_response("hello"),
        ]
    )
    result = _call_with_retry(
        model=MODEL_SONNET,
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        client=fake,
        sleep=lambda _: None,  # no real backoff in tests
    )
    assert result.text == "hello"
    assert fake.messages.create.call_count == 3


def test_call_with_retry_gives_up_after_max_attempts():
    from app.workers.ai_worker import _call_with_retry

    fake = _build_fake_client(
        [
            _make_rate_limit_error(),
            _make_rate_limit_error(),
            _make_rate_limit_error(),
        ]
    )
    with pytest.raises(anthropic.RateLimitError):
        _call_with_retry(
            model=MODEL_SONNET,
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            client=fake,
            sleep=lambda _: None,
        )
    assert fake.messages.create.call_count == 3


# ---------------------------------------------------------------------------
# (d) No retry on validation error
# ---------------------------------------------------------------------------


def test_call_with_retry_does_not_retry_validation_error():
    from app.workers.ai_worker import _call_with_retry

    fake = _build_fake_client([_make_bad_request_error()])
    with pytest.raises(anthropic.BadRequestError):
        _call_with_retry(
            model=MODEL_SONNET,
            system=None,
            messages=[{"role": "user", "content": "hi"}],
            client=fake,
            sleep=lambda _: None,
        )
    assert fake.messages.create.call_count == 1


# ---------------------------------------------------------------------------
# (b) Postgres SKIP LOCKED — only runs if BRIO_PM_TEST_DB is set
# ---------------------------------------------------------------------------


def _have_test_db() -> bool:
    return bool(os.environ.get("BRIO_PM_TEST_DB"))


@pytest.mark.skipif(not _have_test_db(), reason="BRIO_PM_TEST_DB not set; skipping live-Postgres test")
def test_skip_locked_two_workers_dont_double_claim():  # pragma: no cover - integration
    """Two threads each claim_one_job() concurrently; they must claim
    DIFFERENT rows (or one returns None) — never the same row."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session, sessionmaker

    from app.models import AIJob, Project, Client  # noqa: F401 - register tables
    from app.workers.ai_worker import claim_one_job

    url = os.environ["BRIO_PM_TEST_DB"]
    engine = create_engine(url, future=True)
    SessionLocalTest = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    # Seed: one project + two queued jobs.
    with Session(engine) as db:
        # Make a throwaway client + project to satisfy FKs.
        from app.models.client import Client as ClientModel
        from app.models.project import Project as ProjectModel

        client_obj = ClientModel(name="test-client")
        db.add(client_obj)
        db.flush()
        proj = ProjectModel(slug=f"test-{uuid4().hex[:8]}", name="t", client_id=client_obj.id)
        db.add(proj)
        db.flush()
        j1 = AIJob(project_id=proj.id, kind="email_draft", status="queued", input={"prompt": "a"})
        j2 = AIJob(project_id=proj.id, kind="email_draft", status="queued", input={"prompt": "b"})
        db.add_all([j1, j2])
        db.commit()
        seeded_ids = {j1.id, j2.id}

    claimed: list[Any] = []
    barrier = threading.Barrier(2)

    def _worker():
        barrier.wait()
        with SessionLocalTest() as db:
            job = claim_one_job(db)
            claimed.append(job.id if job else None)
            time.sleep(0.05)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Both must have claimed something distinct (no double-claim).
    non_null = [c for c in claimed if c is not None]
    assert len(non_null) == len(set(non_null)), f"double-claim detected: {claimed}"
    assert all(j_id in seeded_ids for j_id in non_null)

    # Cleanup.
    with Session(engine) as db:
        db.execute(text("DELETE FROM ai_jobs WHERE id = ANY(:ids)"), {"ids": list(seeded_ids)})
        db.commit()
