# BRIO Keystone (brio-pm) — Applied-AI Engineering Case Study

**One line:** A production project-management platform for a construction company where LLMs do the load-bearing work — classifying documents, extracting scopes from drawings via vision, and generating itemized estimates — engineered as a real system: async job queue, per-kind model routing, exact cost accounting, truncation handling, an offline eval harness, and 40+ tests.

**Role:** sole architect + engineer. **Stack:** FastAPI · Postgres 16 · SQLAlchemy · a background AI worker · Next.js frontend · Anthropic (Opus/Sonnet/Haiku) · deployed on a VM (Docker) behind nginx.

---

## Context
BRIO is a licensed BC residential builder. brio-pm ("Keystone") runs the back office **and** the client portal. The interesting part isn't CRUD — it's that the AI is not a chatbot bolted on the side; it sits on the critical path of money-adjacent artifacts: *"read this uploaded contract/permit and index it," "turn these drawings into a construction scope," "generate an itemized estimate."* Getting that wrong costs real dollars, so it had to be engineered, not prompted-and-prayed.

## The AI architecture

**1. Async AI-job worker, not inline calls.** AI tasks are rows in an `ai_jobs` table processed by a dedicated worker (`app/workers/ai_worker.py`, ~1.5k LOC) with a retry loop. Kinds: `scope_analysis`, `estimate_generation`, `document_summary`, `communication_classify`, `email_draft`, `ocr`. Decoupling the API from the model means a 60-second Opus vision call never blocks a request, and failures are retried/observable rather than lost.

**2. Per-kind model routing + a cost model** (`app/services/ai.py`). Each job kind maps to the right tier — **Opus** for vision scope analysis, **Sonnet** for estimate/summary generation, **Haiku** for cheap OCR — via a single routing table so the rest of the code never hard-codes a model. Every call returns an `AnthropicResult` with input/output tokens, `stop_reason`, and an **exact USD cost** computed from a Decimal rate card and persisted per job (`Numeric(10,4)`). Cost is a first-class output, not an afterthought.

**3. Vision document extraction with a text-vs-image decision** (`app/services/document_summary_extractor.py`, `drawing/region_extractor.py`). Uploaded documents are normalized before the model sees them: PDFs get PyMuPDF text extraction, but **if the text is sparse (<200 chars) — the signature of a scanned drawing — the pipeline falls back to rendering the page to an image and using the vision path.** Images are downscaled to a bounded edge to cap the base64 payload (and cost). The DPI (110) was chosen deliberately: enough to read a title block / sheet number, not enough to blow the token budget. These are the kinds of cost/quality tradeoffs that separate "calls an LLM" from "runs LLMs in production."

## Hard problems solved (the engineering signal)

- **Silent truncation.** A full multi-room scope exceeded the default 4096 output-token cap and returned *partial, unparseable JSON* — a failure that looks like a bug three layers away. Fix: a per-kind `max_tokens` table (16k for scope/estimate) **and** surfacing `stop_reason` so the system can *detect* truncation instead of choking on it. The eval harness now treats `stop_reason=="max_tokens"` as an automatic fail so this regression can't return quietly.
- **Determinism at the test seam.** `call_anthropic(client=…)` is dependency-injected, so the worker and evals are testable without hitting the API — the whole AI layer has real unit tests (`test_ai_worker.py`, `test_scope_analysis_worker.py`), not just happy-path smoke.
- **Cost/latency as data.** Costs were already tracked; I added wall-clock **latency** capture to the wrapper so the eval harness reports p50/p95 alongside accuracy and spend.

## The eval + observability layer (`app/evals/`)
The differentiator between *"we use Claude"* and *"we engineer LLM systems"*: an **offline eval harness** that regression-tests model outputs.

- **Golden cases** (`cases/*.jsonl`, synthetic) → **deterministic, kind-specific scorers** (recall on set-valued fields, truncation as a hard gate) → a **report with pass-rate, mean score, total cost, and p50/p95 latency**, exiting non-zero when the suite goes red (a CI gate).
- Runs with **no API key** via an injected mock invoke fn (`python -m app.evals --mock`), and against the real model with `--live`. Covered by `tests/test_evals.py` (7 tests, all green).
- Effect: changing a prompt or swapping a model becomes a *measured* change — accuracy/cost/latency drift is visible before it ships, not after a client gets a wrong estimate.

## Retrieval (RAG) — the "Rio" knowledge layer (`app/services/rag.py`)
Semantic search over project documents: chunk → embed → **pgvector** cosine retrieval → a grounded, **cited** answer ("answer only from these sources or say you don't know"). The embedder and vector store are injected behind Protocols, so the retrieval/ranking logic is unit-tested offline with a deterministic feature-hashing embedder + in-memory store (no API key, no DB), while production swaps in Voyage embeddings + pgvector unchanged. Retrieval quality is itself an eval kind (`rag`): recall on the doc that holds the answer, plus answer-grounding — so a chunking or embedding change is a *measured* change.

## Scale & rigor
- ~180 Python + ~150 TS files; **41 backend test files** including the AI worker and scope analysis; the platform is **in daily production use** (office + client portal) on a VM behind nginx, with an owner portal (live progress, messaging, selections, payments, warranty).
- Third-party integration under real constraints: R2 object storage, geocoding, PDF generation/branding, an email pipeline.

## What this demonstrates for an Applied-AI / Forward-Deployed Engineer
| Competency | Evidence here |
|---|---|
| LLMs in production, not demos | Async job worker on the critical path, daily use, real $ outputs |
| Cost/latency engineering | Per-kind model routing, Decimal cost model, latency capture, budgeted vision DPI/downscale |
| Multimodal | Text-vs-image fallback + vision extraction of scanned drawings |
| Reliability | Truncation detection, retries, `stop_reason` surfacing |
| **Evaluation & observability** | Offline eval harness w/ golden cases, deterministic scoring, regression gate, cost/latency reporting |
| Testability | Dependency-injected model client; 40+ tests incl. AI paths |
| Ownership | Sole architect: data model → worker → prompts → evals → deploy |

*Repo is private (contains client data). This writeup + the eval harness (`app/evals/`) are the sanitized, shareable artifacts.*
