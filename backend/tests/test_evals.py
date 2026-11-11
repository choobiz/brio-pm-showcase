"""Tests for the LLM eval harness (app/evals).

Runs entirely offline via injected invoke fns — no API key, no DB, no network.
Proves the scoring, aggregation, truncation gate, and regression signal work.
"""

from __future__ import annotations

from pathlib import Path

from app.evals import GoldenCase, run_suite, load_cases, render_markdown
from app.evals.harness import InvokeOutput


def _case(kind, expect, input=None):
    return GoldenCase(id=f"t-{kind}", kind=kind, input=input or {}, expect=expect)


def test_perfect_output_passes():
    case = _case("document_summary",
                 {"doc_type": "contract", "summary": "ok", "key_terms": ["fixed price", "warranty"]})

    def invoke(_c):
        return InvokeOutput(output=dict(case.expect), cost_usd=0.01, latency_ms=800,
                            stop_reason="end_turn")

    rep = run_suite([case], invoke)
    assert rep.n == 1
    assert rep.results[0].passed is True
    assert rep.results[0].score >= 0.8
    assert rep.green is True


def test_missing_key_terms_lowers_score():
    case = _case("document_summary",
                 {"doc_type": "contract", "summary": "ok",
                  "key_terms": ["fixed price", "warranty", "completion", "deposit"]})

    def invoke(_c):
        # right type + summary, but only 1 of 4 key terms → recall 0.25
        return InvokeOutput(output={"doc_type": "contract", "summary": "ok",
                                    "key_terms": ["fixed price"]},
                            cost_usd=0.01, latency_ms=500, stop_reason="end_turn")

    rep = run_suite([case], invoke)
    r = rep.results[0]
    # 0.4 (type) + 0.4*0.25 (terms) + 0.2 (summary) = 0.70 → below 0.8 threshold
    assert abs(r.score - 0.70) < 1e-6
    assert r.passed is False
    assert rep.green is False


def test_scope_truncation_hard_fails():
    case = _case("scope_analysis", {"categories": ["Demolition", "Plumbing"]})

    def invoke(_c):
        return InvokeOutput(output={"categories": [{"name": "Demolition", "items": ["x"]}]},
                            cost_usd=0.05, latency_ms=3000, stop_reason="max_tokens")

    rep = run_suite([case], invoke)
    assert rep.results[0].score == 0.0
    assert rep.results[0].detail.get("truncated") is True
    assert rep.results[0].passed is False


def test_scope_recall_and_items():
    case = _case("scope_analysis", {"categories": ["Demolition", "Plumbing", "Electrical"]})

    def invoke(_c):
        return InvokeOutput(output={"categories": [
            {"name": "Demolition", "items": ["demo tile"]},
            {"name": "Plumbing", "items": ["rough-in"]},
            {"name": "Electrical", "items": ["pot lights"]},
        ]}, cost_usd=0.04, latency_ms=2200, stop_reason="end_turn")

    rep = run_suite([case], invoke)
    assert rep.results[0].score == 1.0
    assert rep.results[0].passed is True


def test_invoke_error_fails_case_not_suite():
    good = _case("document_summary", {"doc_type": "invoice", "summary": "s", "key_terms": ["a"]})
    bad = _case("scope_analysis", {"categories": ["Demolition"]})

    def invoke(c):
        if c.kind == "scope_analysis":
            raise RuntimeError("boom")
        return InvokeOutput(output=dict(good.expect), cost_usd=0.0, latency_ms=100,
                            stop_reason="end_turn")

    rep = run_suite([good, bad], invoke)  # one raises — suite still completes
    assert rep.n == 2
    assert rep.pass_rate == 0.5
    assert rep.green is False
    assert rep.results[1].error is not None


def test_aggregates_and_percentiles():
    cases = [_case("document_summary", {"doc_type": "contract", "summary": "s", "key_terms": ["a"]})
             for _ in range(3)]
    lats = iter([100, 500, 900])
    costs = iter([0.01, 0.02, 0.03])

    def invoke(c):
        return InvokeOutput(output=dict(c.expect), cost_usd=next(costs),
                            latency_ms=next(lats), stop_reason="end_turn")

    rep = run_suite(cases, invoke)
    assert rep.total_cost_usd == 0.06
    assert rep.latency_pctl(50) == 500
    assert rep.latency_pctl(95) in (900,)  # top of a 3-sample set
    assert "GREEN" in render_markdown(rep)


def test_load_seed_cases():
    cases_dir = Path(__file__).resolve().parent.parent / "app" / "evals" / "cases"
    cases = load_cases(cases_dir)
    kinds = {c.kind for c in cases}
    assert "document_summary" in kinds and "scope_analysis" in kinds
    assert all(c.id and c.expect for c in cases)
