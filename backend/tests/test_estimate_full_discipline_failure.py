"""A trade that cannot be priced fails the whole estimate_full run.

2026-10-22, 456 W Sample: the architectural trade of two commercial TI
estimates ran past the 16k output cap, was retried with the SAME cap, failed
again, and was dropped. Both runs still finished `done` with a plausible total,
and the first one became the project's current estimate with no walls,
ceilings, floors, doors or millwork in it.

Two regressions are pinned here:
  1. A cut-off answer is retried with MORE room, not the same call again.
  2. A trade still unusable after that fails the job and saves no estimate.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text as _T

from app.services.ai import AnthropicResult
from app.workers import ai_worker as w

GOOD = '{"categories": [{"name": "09 Finishes", "items": [{"name": "Paint", "labor": 100, "materials": 50}]}]}'


def _result(text: str, *, stop_reason: str = "end_turn", out_tokens: int = 100):
    return AnthropicResult(text=text, input_tokens=10, output_tokens=out_tokens,
                           cost_usd=Decimal("0.01"), model="m", stop_reason=stop_reason)


@pytest.fixture
def calls(monkeypatch):
    recorded: list[dict] = []
    script: list[AnthropicResult] = []

    def _fake(*, model, system, messages, max_tokens, client=None, **kw):
        recorded.append({"max_tokens": max_tokens, "messages": messages})
        return script[min(len(recorded) - 1, len(script) - 1)]

    monkeypatch.setattr(w, "_call_with_retry", _fake)
    return {"recorded": recorded, "script": script}


def _price(max_tokens=16000, approved=None):
    meter = {"in": 0, "out": 0, "usd": Decimal(0)}
    out = w._price_discipline(disc="architectural", model="m", system="s",
                              messages=[{"role": "user", "content": "price it"}],
                              max_tokens=max_tokens, approved=approved, meter=meter)
    return out, meter


class TestCutOffGetsMoreRoom:
    def test_retry_after_a_cut_off_asks_for_more_tokens(self, calls):
        calls["script"] += [_result('{"categories": [{"name": "09', stop_reason="max_tokens",
                                    out_tokens=16000),
                            _result(GOOD)]
        parsed, meter = _price()
        assert parsed["categories"][0]["name"] == "09 Finishes"
        assert [c["max_tokens"] for c in calls["recorded"]] == [16000, w.ESTIMATE_DISCIPLINE_MAX_TOKENS], \
            "the retry must not repeat the call that was just cut off"
        assert meter["usd"] == Decimal("0.02"), "both attempts are paid for and recorded"

    def test_a_cut_off_at_the_ceiling_is_not_retried_pointlessly(self, calls):
        cap = w.ESTIMATE_DISCIPLINE_MAX_TOKENS
        calls["script"].append(_result('{"categories": [', stop_reason="max_tokens", out_tokens=cap))
        with pytest.raises(w.DisciplineFailed):
            _price(max_tokens=cap)
        assert len(calls["recorded"]) == 1

    def test_malformed_but_complete_output_gets_a_repair_prompt(self, calls):
        calls["script"] += [_result("Here is the estimate: not json"), _result(GOOD)]
        parsed, _ = _price()
        assert parsed
        second = calls["recorded"][1]
        assert second["max_tokens"] == 16000, "malformed output needs a correction, not more room"
        assert "could not be parsed" in second["messages"][-1]["content"]


class TestUnusableTradeRaises:
    def test_two_bad_answers_raise_and_name_the_trade(self, calls):
        calls["script"] += [_result("nope"), _result("still nope")]
        with pytest.raises(w.DisciplineFailed, match="architectural"):
            _price()

    def test_the_failure_keeps_evidence_of_what_the_answer_looked_like(self, calls):
        """'Cut off at 32k' cannot tell a long estimate from a model stuck in a
        loop. The size and the end of the answer can, so they are kept."""
        loop = '{"categories": [{"name": "09", "items": [{"name": "' + "ab" * 3000
        calls["script"] += [_result(loop, stop_reason="max_tokens", out_tokens=16000),
                            _result(loop, stop_reason="max_tokens", out_tokens=32000)]
        with pytest.raises(w.DisciplineFailed) as info:
            _price()
        ev = info.value.evidence
        assert ev["discipline"] == "architectural"
        assert ev["stop_reason"] == "max_tokens" and ev["output_tokens"] == 32000
        assert ev["chars"] == len(loop)
        assert ev["tail"].endswith("abab") and ev["head"].startswith('{"categories"')


def test_a_failed_trade_fails_the_job_and_saves_no_estimate(db_session, monkeypatch):
    pid = uuid4()
    db_session.execute(_T("insert into projects (id, name, slug, status) values (:i, 'DF', :s, 'lead')"),
                       {"i": str(pid), "s": f"df-{pid.hex[:8]}"})
    job = w.AIJob(project_id=pid, kind="estimate_full", status="running", input={})
    db_session.add(job)
    db_session.flush()
    for name in ("plan", "begin", "finish"):
        monkeypatch.setattr(w._progress, name, lambda *a, **k: None)
    import app.services.estimate_readiness as ER

    monkeypatch.setattr(ER, "compute_readiness", lambda db, p: {"takeoff_mode": "none", "confidence": "low"})
    monkeypatch.setattr(w, "_contingency_for_project", lambda db, p: None)
    monkeypatch.setattr(w, "_merge_estimate_inputs", lambda db, p, i: dict(i))
    monkeypatch.setattr(w, "build_messages_for_job", lambda kind, inp: (None, [], 16000))
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr(db_session, "rollback", lambda: None)
    saved = []
    monkeypatch.setattr(db_session, "execute", _record_inserts(db_session.execute, saved))

    def fake_price(*, disc, meter, **kw):
        meter["usd"] += Decimal("0.10")
        if disc == "architectural":
            raise w.DisciplineFailed("architectural produced no usable estimate after 2 attempts: cut off",
                                     {"discipline": "architectural", "chars": 18807, "tail": "abab"})
        return {"categories": [{"name": "26 Electrical", "items": [{"name": "Lights", "labor": 10, "materials": 10}]}]}

    monkeypatch.setattr(w, "_price_discipline", fake_price)

    w._run_estimate_full(db_session, job, {"scope_id": "s", "method": "scope", "detail_level": "fully_itemized",
                                           "disciplines": ["electrical", "architectural", "plumbing"]})

    assert job.status == "error", "a run missing a trade must not finish as done"
    assert "architectural" in (job.error or ""), "the error must say which trade failed"
    assert not saved, "nothing incomplete may be saved"
    assert job.cost_usd == Decimal("0.20"), "what was spent before the failure is still recorded"
    assert job.output["failed_discipline"]["chars"] == 18807, "the evidence is kept on the job"


def _record_inserts(real, saved):
    def execute(stmt, *a, **k):
        if "insert into estimate_versions" in str(stmt).lower():
            saved.append(stmt)
        return real(stmt, *a, **k)
    return execute


def test_long_answers_get_a_longer_timeout():
    """A 32k-token answer can outlast the SDK's 600s default, and a timeout is
    retried, so the same long answer would be paid for up to three times."""
    from types import SimpleNamespace

    from app.services import ai

    seen = {}

    class Client:
        def __init__(self, timeout=None):
            self.timeout = timeout
            self.messages = SimpleNamespace(create=self._create)

        def with_options(self, timeout):
            return Client(timeout)

        def _create(self, **kw):
            seen.setdefault("timeouts", []).append(self.timeout)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")],
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                                   stop_reason="end_turn")

    ai.call_anthropic("claude-sonnet-5-5", None, [{"role": "user", "content": "x"}], max_tokens=16000, client=Client())
    ai.call_anthropic("claude-sonnet-5-5", None, [{"role": "user", "content": "x"}], max_tokens=32000, client=Client())
    assert seen["timeouts"] == [None, ai.LONG_OUTPUT_TIMEOUT_S]
