"""Eval runner: golden cases → scored regression report.

    invoke_fn(case) -> InvokeOutput      # you inject this
    run_suite(cases, invoke_fn) -> SuiteReport
    render_markdown(report) -> str

`invoke_fn` is the seam. In CI/tests, inject a stub that returns canned
outputs (no API key, deterministic). In a real run, use `live_invoke`
(below), which builds the production prompt for the case's kind and calls
`app.services.ai.call_anthropic`.

CLI:
    python -m app.evals.harness --cases app/evals/cases --mock
    python -m app.evals.harness --cases app/evals/cases --live --out /tmp/eval.md
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.evals.scorers import SCORERS


# ── schema ────────────────────────────────────────────────────────────

@dataclass
class GoldenCase:
    id: str
    kind: str                       # "document_summary" | "scope_analysis" | …
    input: dict[str, Any]           # what the model receives (text / image ref / etc.)
    expect: dict[str, Any]          # assertions the scorer checks
    notes: str = ""


@dataclass
class InvokeOutput:
    output: dict[str, Any]          # PARSED model output (dict)
    raw: str = ""
    cost_usd: float = 0.0
    latency_ms: int = 0
    stop_reason: str | None = None
    error: str | None = None


@dataclass
class CaseResult:
    case_id: str
    kind: str
    score: float
    passed: bool
    cost_usd: float
    latency_ms: int
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class SuiteReport:
    results: list[CaseResult]
    pass_threshold: float

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        return (sum(r.passed for r in self.results) / self.n) if self.n else 0.0

    @property
    def mean_score(self) -> float:
        return statistics.mean(r.score for r in self.results) if self.n else 0.0

    @property
    def total_cost_usd(self) -> float:
        return round(sum(r.cost_usd for r in self.results), 4)

    def latency_pctl(self, p: float) -> int:
        lat = sorted(r.latency_ms for r in self.results if r.latency_ms)
        if not lat:
            return 0
        k = min(len(lat) - 1, int(round((p / 100) * (len(lat) - 1))))
        return lat[k]

    @property
    def green(self) -> bool:
        """Suite passes iff every case passes. Used as the CI exit signal."""
        return self.n > 0 and all(r.passed for r in self.results)


InvokeFn = Callable[[GoldenCase], InvokeOutput]


# ── runner ────────────────────────────────────────────────────────────

def run_suite(
    cases: list[GoldenCase],
    invoke_fn: InvokeFn,
    pass_threshold: float = 0.8,
) -> SuiteReport:
    results: list[CaseResult] = []
    for case in cases:
        scorer = SCORERS.get(case.kind)
        if scorer is None:
            results.append(CaseResult(case.id, case.kind, 0.0, False, 0.0, 0,
                                      error=f"no scorer for kind '{case.kind}'"))
            continue
        try:
            out = invoke_fn(case)
        except Exception as exc:  # invoke itself blew up → fail the case, keep going
            results.append(CaseResult(case.id, case.kind, 0.0, False, 0.0, 0,
                                      error=f"invoke error: {exc!r}"))
            continue
        if out.error:
            results.append(CaseResult(case.id, case.kind, 0.0, False,
                                      out.cost_usd, out.latency_ms, error=out.error))
            continue
        meta = {"stop_reason": out.stop_reason}
        score, detail = scorer(out.output, case.expect, meta)
        results.append(CaseResult(
            case_id=case.id, kind=case.kind, score=round(score, 4),
            passed=score >= pass_threshold, cost_usd=out.cost_usd,
            latency_ms=out.latency_ms, detail=detail,
        ))
    return SuiteReport(results=results, pass_threshold=pass_threshold)


# ── loading ───────────────────────────────────────────────────────────

def load_cases(path: str | Path) -> list[GoldenCase]:
    """Load golden cases from a .jsonl file or a directory of .jsonl files.
    One JSON object per line: {id, kind, input, expect, notes?}."""
    p = Path(path)
    files = sorted(p.glob("*.jsonl")) if p.is_dir() else [p]
    cases: list[GoldenCase] = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            d = json.loads(line)
            cases.append(GoldenCase(
                id=d["id"], kind=d["kind"], input=d.get("input", {}),
                expect=d.get("expect", {}), notes=d.get("notes", ""),
            ))
    return cases


# ── report ────────────────────────────────────────────────────────────

def render_markdown(report: SuiteReport) -> str:
    by_kind: dict[str, list[CaseResult]] = {}
    for r in report.results:
        by_kind.setdefault(r.kind, []).append(r)

    lines = [
        "# LLM Eval Report",
        "",
        f"- **Cases:** {report.n}  ·  **Pass rate:** {report.pass_rate*100:.0f}%"
        f"  ·  **Mean score:** {report.mean_score:.2f}  (threshold {report.pass_threshold:.2f})",
        f"- **Cost:** ${report.total_cost_usd:.4f}"
        f"  ·  **Latency p50/p95:** {report.latency_pctl(50)}ms / {report.latency_pctl(95)}ms",
        f"- **Suite:** {'🟢 GREEN' if report.green else '🔴 RED'}",
        "",
        "| case | kind | score | pass | $ | ms | detail |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report.results:
        d = r.error or json.dumps(r.detail, separators=(',', ':'))
        if len(d) > 90:
            d = d[:87] + "…"
        lines.append(
            f"| {r.case_id} | {r.kind} | {r.score:.2f} | "
            f"{'✅' if r.passed else '❌'} | {r.cost_usd:.4f} | {r.latency_ms} | {d} |"
        )
    lines.append("")
    for kind, rs in by_kind.items():
        pr = sum(x.passed for x in rs) / len(rs)
        lines.append(f"- **{kind}**: {sum(x.passed for x in rs)}/{len(rs)} pass ({pr*100:.0f}%)")
    return "\n".join(lines)


# ── live wiring (only imported/used in --live mode) ───────────────────

def live_invoke(case: GoldenCase) -> InvokeOutput:
    """Production invoke: build the real prompt for `case.kind` and call the
    Anthropic wrapper. Lazy-imports so `import app.evals` stays key/DB-free."""
    import json as _json
    from app.services.ai import call_anthropic, model_for_kind, max_tokens_for_kind

    system, messages = _build_prompt(case)
    res = call_anthropic(
        model=model_for_kind(case.kind),
        system=system,
        messages=messages,
        max_tokens=max_tokens_for_kind(case.kind),
    )
    try:
        parsed = _json.loads(_strip_fences(res.text))
    except Exception:
        # Non-JSON kinds (e.g. summaries returned as prose) → wrap.
        parsed = {"summary": res.text}
    return InvokeOutput(
        output=parsed, raw=res.text, cost_usd=float(res.cost_usd),
        latency_ms=res.latency_ms, stop_reason=res.stop_reason,
    )


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0]
    return s.strip()


def _build_prompt(case: GoldenCase) -> tuple[str | None, list[dict[str, Any]]]:
    """Minimal prompt builder for live eval. Kept thin on purpose — the point
    of the harness is to exercise the SAME prompts production uses, so wire
    these to the real prompt modules (document_summary_prompts, scope prompts)
    as they stabilise. For now, a text-in / JSON-out contract."""
    text = case.input.get("text", "")
    if case.kind == "document_summary":
        # Wire to the REAL production system prompt so --live exercises exactly
        # what the worker sends, not a paraphrase.
        from app.services.document_summary_prompts import _SYSTEM_BASE
        system = _SYSTEM_BASE + ' Include a top-level "doc_type" and a "key_terms" array.'
    elif case.kind == "scope_analysis":
        # Real production scope prompt (cost categories + checklist + JSON schema).
        from app.services.ai import _SCOPE_SYSTEM_PROMPT
        system = _SCOPE_SYSTEM_PROMPT
    else:
        system = "Return a concise JSON object."
    return system, [{"role": "user", "content": text}]


# ── CLI ───────────────────────────────────────────────────────────────

def _mock_invoke(case: GoldenCase) -> InvokeOutput:
    """Deterministic stub: synthesise a *correctly-shaped, passing* output from
    the golden `expect` (no API key). Proves the harness/report path end-to-end.
    Kind-aware because a golden `expect` block is written for the scorer, which
    is not always the model's output shape (e.g. scope categories)."""
    if case.kind == "scope_analysis":
        names = case.expect.get("categories", [])
        output = {"categories": [{"name": n, "items": ["(mock item)"]} for n in names]}
    elif case.kind == "rag":
        output = {"retrieved": list(case.expect.get("must_retrieve", [])),
                  "answer": " ".join(case.expect.get("answer_must_include", []))}
    else:
        output = dict(case.expect)
    return InvokeOutput(output=output, cost_usd=0.0, latency_ms=1, stop_reason="end_turn")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="brio-pm LLM eval harness")
    ap.add_argument("--cases", default="app/evals/cases")
    ap.add_argument("--live", action="store_true", help="call Anthropic (needs key)")
    ap.add_argument("--mock", action="store_true", help="deterministic stub (default)")
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--out", default=None, help="write markdown report to this path")
    args = ap.parse_args(argv)

    cases = load_cases(args.cases)
    invoke = live_invoke if args.live else _mock_invoke
    report = run_suite(cases, invoke, pass_threshold=args.threshold)
    md = render_markdown(report)
    print(md)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    return 0 if report.green else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
