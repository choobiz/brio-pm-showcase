# LLM Eval Harness (`app.evals`)

Regression-tests brio-pm's LLM outputs so a prompt or model change can't silently
degrade quality. Golden cases → deterministic scoring → a report with
**accuracy, cost, and p50/p95 latency**. Runs in CI with **no API key**.

## Why
The AI worker (`app/workers/ai_worker.py`) drives money-adjacent outputs —
document classification, scope extraction, estimate generation. Without evals,
"improving a prompt" is a vibe. This turns it into a measured, gated change.

## Design
- **Seam = `invoke_fn(case) -> InvokeOutput`.** CI injects a deterministic mock
  (`--mock`); a real run (`--live`) builds the production prompt and calls
  `app.services.ai.call_anthropic`. The harness core imports only stdlib, so
  `import app.evals` needs no key, DB, or network.
- **Scorers are pure + kind-specific** (`scorers.py`):
  - `document_summary` — doc-type classification (0.4) + key-term **recall** (0.4)
    + summary sanity (0.2).
  - `scope_analysis` — **truncation is a hard fail** (`stop_reason=="max_tokens"`
    → 0.0; this failure mode is real, see `ai.py` `KIND_MAX_TOKENS`), then
    category recall (0.7) + items-present (0.3).
  - Recall (not exact-match) on set fields: the model may legitimately add
    items; we penalise **misses**, not extras.
- **Suite is GREEN iff every case passes** — the process exit code (0/1) is the
  CI signal.

## Run
```bash
# offline smoke (no key) — proves the harness path
python -m app.evals --cases app/evals/cases --mock

# real run against Anthropic (needs ANTHROPIC_API_KEY)
python -m app.evals --cases app/evals/cases --live --out /tmp/eval.md

# the regression test (part of pytest, no key)
pytest tests/test_evals.py -q
```

## Add a golden case
Append one JSON object per line to `cases/<kind>.jsonl`:
```json
{"id":"ds-contract-02","kind":"document_summary","input":{"text":"…"},"expect":{"doc_type":"contract","key_terms":["…"]}}
```
Inputs must be **synthetic / sanitised** — no client data in the repo.

## Extend
- New kind → add a scorer to `SCORERS` + a `cases/<kind>.jsonl`.
- Wire `_build_prompt()` (harness) to the real prompt modules
  (`document_summary_prompts.py`, scope prompts) so `--live` exercises the exact
  production prompt, not a paraphrase.
- Persist reports over time → track accuracy/cost/latency drift per model+prompt.
