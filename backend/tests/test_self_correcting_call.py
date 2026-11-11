"""Bounded self-correction on output that arrives fine and will not parse.

`_call_with_retry` handles TRANSPORT failure (429/5xx/timeout). This covers the
other half: a 200 whose body is unusable, which today is terminal.

No database, no network, no client. `_call_with_retry` is the single seam.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.ai import AnthropicResult
from app.workers import ai_worker as w


def _result(text: str, *, stop_reason: str = "end_turn", out_tokens: int = 100):
    return AnthropicResult(text=text, input_tokens=10, output_tokens=out_tokens,
                           cost_usd=Decimal("0.01"), model="m", stop_reason=stop_reason)


@pytest.fixture
def calls(monkeypatch):
    """Record every call and reply from a script. Nothing reaches the network."""
    recorded: list[dict] = []
    script: list[AnthropicResult] = []

    def _fake(*, model, system, messages, max_tokens, client=None, **kw):
        recorded.append({"max_tokens": max_tokens, "messages": messages})
        return script[min(len(recorded) - 1, len(script) - 1)]

    monkeypatch.setattr(w, "_call_with_retry", _fake)
    monkeypatch.setattr(w, "MAX_REPAIR_ATTEMPTS", 2)
    return {"recorded": recorded, "script": script}


def _run(kind="document_summary", max_tokens=800):
    return w._call_self_correcting(kind=kind, model="m", system="s",
                                   messages=[{"role": "user", "content": "go"}],
                                   max_tokens=max_tokens)


class TestDarkByDefault:
    def test_a_kind_not_enabled_is_called_exactly_once(self, calls, monkeypatch):
        """The whole feature must be invisible until a kind is switched on."""
        monkeypatch.setattr(w, "SELF_CORRECT_KINDS", set())
        calls["script"].append(_result("not json at all"))

        out = _run()

        assert len(calls["recorded"]) == 1, "no repair may happen for a disabled kind"
        assert out.text == "not json at all", "the bad result passes through untouched"


class TestTruncationEscalatesTheBudget:
    """The finding that measurement produced: truncation needs MORE room, not less."""

    def test_budget_doubles_and_the_prompt_is_left_alone(self, calls, monkeypatch):
        monkeypatch.setattr(w, "SELF_CORRECT_KINDS", {"document_summary"})
        calls["script"].extend([
            _result('{"a": "unterminated', stop_reason="max_tokens", out_tokens=800),
            _result('{"a": "complete"}'),
        ])

        out = _run(max_tokens=800)

        assert [c["max_tokens"] for c in calls["recorded"]] == [800, 1600]
        assert out.text == '{"a": "complete"}'
        second = calls["recorded"][1]["messages"]
        assert len(second) == 1, (
            "a truncated model must not be told to be briefer — that discards "
            "the content it ran out of room to write")

    def test_escalation_is_capped(self, calls, monkeypatch):
        monkeypatch.setattr(w, "SELF_CORRECT_KINDS", {"document_summary"})
        monkeypatch.setattr(w, "MAX_ESCALATED_TOKENS", 4000)
        calls["script"].append(
            _result("{trunc", stop_reason="max_tokens", out_tokens=3000))

        _run(max_tokens=3000)

        assert all(c["max_tokens"] <= 4000 for c in calls["recorded"]), \
            "a repair loop must not be able to bill without bound"

    def test_gives_up_when_the_budget_cannot_be_raised(self, calls, monkeypatch):
        """At the ceiling, retrying is spend with no new outcome."""
        monkeypatch.setattr(w, "SELF_CORRECT_KINDS", {"document_summary"})
        monkeypatch.setattr(w, "MAX_ESCALATED_TOKENS", 1000)
        calls["script"].append(
            _result("{trunc", stop_reason="max_tokens", out_tokens=900))

        _run(max_tokens=1000)

        assert len(calls["recorded"]) == 1


class TestMalformedOutputRepairsThePrompt:
    def test_the_parser_error_is_fed_back(self, calls, monkeypatch):
        monkeypatch.setattr(w, "SELF_CORRECT_KINDS", {"scope_analysis"})
        calls["script"].extend([_result("Sure! Here you go:"), _result('{"ok": 1}')])

        out = _run(kind="scope_analysis")

        second = calls["recorded"][1]["messages"]
        assert len(second) == 3, "the bad reply and a correction are appended"
        assert second[1]["role"] == "assistant"
        assert "JSON object" in second[2]["content"]
        assert out.text == '{"ok": 1}'

    def test_a_json_array_is_repaired_not_accepted(self, calls, monkeypatch):
        monkeypatch.setattr(w, "SELF_CORRECT_KINDS", {"scope_analysis"})
        calls["script"].extend([_result("[1, 2]"), _result('{"ok": 1}')])

        assert _run(kind="scope_analysis").text == '{"ok": 1}'
        assert len(calls["recorded"]) == 2

    def test_budget_is_not_escalated_for_a_parse_failure(self, calls, monkeypatch):
        monkeypatch.setattr(w, "SELF_CORRECT_KINDS", {"scope_analysis"})
        calls["script"].extend([_result("nope"), _result('{"ok": 1}')])

        _run(kind="scope_analysis", max_tokens=6000)

        assert [c["max_tokens"] for c in calls["recorded"]] == [6000, 6000]


class TestBounds:
    def test_a_persistently_broken_model_stops_at_the_cap(self, calls, monkeypatch):
        """The regression that protects the bill."""
        monkeypatch.setattr(w, "SELF_CORRECT_KINDS", {"scope_analysis"})
        calls["script"].append(_result("never valid"))

        out = _run(kind="scope_analysis")

        assert len(calls["recorded"]) == 3, "1 initial + MAX_REPAIR_ATTEMPTS(2)"
        assert out.text == "never valid", (
            "the LAST result is returned unchanged so the existing per-kind error "
            "path still reports the real failure")

    def test_valid_first_time_costs_exactly_one_call(self, calls, monkeypatch):
        monkeypatch.setattr(w, "SELF_CORRECT_KINDS", {"scope_analysis"})
        calls["script"].append(_result('{"ok": 1}'))

        assert _run(kind="scope_analysis").text == '{"ok": 1}'
        assert len(calls["recorded"]) == 1


class TestRepairDirective:
    def test_truncation_asks_for_room_and_parse_failure_asks_for_shape(self):
        instruction, escalated = w._repair_directive(
            "truncated", _result("{", stop_reason="max_tokens", out_tokens=800))
        assert instruction is None and escalated == 1600

        instruction, escalated = w._repair_directive(
            "Expecting value", _result("prose"))
        assert escalated is None and "JSON object" in instruction


class TestTokenBudgetHasOneSourceOfTruth:
    """Regression: the prompt builder used to hard-code 800/600.

    Raising KIND_MAX_TOKENS["document_summary"] from 800 to 2000 therefore
    changed nothing, and the next re-run of the same document truncated at
    exactly 800 again. Caught only by re-running a real failure.
    """

    def test_text_mode_uses_the_kind_budget(self, monkeypatch):
        from app.services import ai
        from app.services.document_summary_extractor import ExtractionContext
        from app.services.document_summary_prompts import (
            build_messages_for_document_summary,
        )

        monkeypatch.setitem(ai.KIND_MAX_TOKENS, "document_summary", 2000)
        _, _, max_tokens = build_messages_for_document_summary(
            project_context={}, document={"filename": "x.pdf", "category": "other"},
            extraction=ExtractionContext(mode="text", text="hello", notes=[]))
        assert max_tokens == 2000, "the builder must not shadow KIND_MAX_TOKENS"

    def test_image_mode_keeps_its_smaller_share(self, monkeypatch):
        from app.services import ai
        from app.services.document_summary_extractor import ExtractionContext
        from app.services.document_summary_prompts import (
            build_messages_for_document_summary,
        )

        monkeypatch.setitem(ai.KIND_MAX_TOKENS, "document_summary", 2000)
        _, _, max_tokens = build_messages_for_document_summary(
            project_context={}, document={"filename": "x.png", "category": "photo"},
            extraction=ExtractionContext(mode="image", text="", notes=[],
                                         image_b64="aGk=", image_media_type="image/png"))
        assert max_tokens == 1500, "an image extraction keeps the 0.75 share"
