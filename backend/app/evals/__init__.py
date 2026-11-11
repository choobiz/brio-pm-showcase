"""Offline evaluation harness for brio-pm's LLM outputs.

Turns "we call Claude" into "we can prove, on every prompt/model change,
whether the AI outputs still meet a bar." Golden cases + deterministic
scoring + a regression report (accuracy, cost, p50/p95 latency).

Runnable without an API key via an injected invoke fn (mock in CI); a
`live` mode wires the real prompts + `call_anthropic`.

See README.md.
"""

from app.evals.harness import (
    GoldenCase,
    CaseResult,
    SuiteReport,
    run_suite,
    load_cases,
    render_markdown,
)

__all__ = [
    "GoldenCase",
    "CaseResult",
    "SuiteReport",
    "run_suite",
    "load_cases",
    "render_markdown",
]
