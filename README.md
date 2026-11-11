# brio-pm (Keystone)

An estimating and project management platform I built for BRIO Construction, a licensed residential builder in North Vancouver, BC. It runs the office side of the business (clients, drawings, quantity takeoff, estimates, contracts, schedules, invoices) and a client portal where owners review and accept proposals.

This repository is a sanitized snapshot of a private production system, published so people can read the code. It has a single commit and no history. Client names, street addresses, emails, phone numbers, permit numbers and infrastructure details were replaced with placeholders such as "Sample Client" and "1234 Example Way". Deployment config, security runbooks, internal reports and real documents were left out. Many code comments still refer to real incidents ("on 456 W Sample, 2026-10-26 ..."); the project names in those comments are placeholders, the incidents are real.

I am the only developer, and I also wrote the product requirements as the business owner.

## Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy 2, Alembic (89 migrations), Postgres 16 with pgvector
- AI: Anthropic Claude (Opus, Sonnet, Haiku routed per job type), Voyage embeddings for retrieval
- Drawings: ezdxf for DWG/DXF, PyMuPDF for vector PDFs, numpy for raster room measurement
- Documents: WeasyPrint for PDFs (estimates, scopes, contracts, invoices, client packages)
- Frontend: Next.js 16 (App Router), React 19, TypeScript, Tailwind 4, Auth.js (NextAuth v5)
- Storage: Cloudflare R2 (S3 API); email through AWS SES

## Layout

```
backend/
  app/
    routers/      FastAPI endpoints, one module per area (estimates, drawings, invoices, portal, ...)
    services/     business logic; drawing/ holds the CAD and PDF takeoff engine
    models/       SQLAlchemy models
    workers/      ai_worker.py, the background job runner for all model calls
    evals/        offline evaluation harness for LLM outputs
  alembic/        schema migrations
  tests/          pytest suite (about 1,340 test cases across 110 files)
  scripts/        a few maintenance scripts that tests import
frontend/
  src/app/        Next.js routes: office app under (authed)/, field app, client portal
  src/lib/        typed API clients and pure helpers
  scripts/        node check scripts that run before every build
docs/             design notes for the takeoff engine and the drawing-analysis gate
.github/workflows/ci.yml
```

## What to look at

### AI calls run as queued jobs, with cost recorded per call

`backend/app/workers/ai_worker.py` drains an `ai_jobs` table. It claims one row with `SELECT ... FOR UPDATE SKIP LOCKED`, commits the status change before calling the model so a long request does not hold a lock, retries on 429/5xx, and shuts down cleanly on SIGTERM. `backend/app/services/ai.py` maps each job kind to a model and a `max_tokens` limit, and computes the USD cost of each call from a Decimal rate card. The worker stores token counts and cost with the job, and a truncated response (`stop_reason == "max_tokens"`) is surfaced instead of being parsed as if it were complete.

### Grounded answers and retrieval

- `backend/app/services/assistant.py` is the "Ask" panel. It is a tool-use loop over read-only tools that wrap the same services and access checks the pages use, so the model can only see projects the person asking can see. Each tool returns `sources` (label, link, last updated), and anything the system has no record of goes in a `not_recorded` list so the model has to say it is missing rather than treat silence as good news.
- `backend/app/services/rag.py` is a retrieval layer: chunking, an embedder and vector store behind Protocols, a pgvector store (`alembic/versions/0043_document_chunks_pgvector.py`), and a grounded prompt that cites the retrieved chunks. The ranking logic is tested offline with a fake embedder and an in-memory store (`tests/test_rag.py`). In this snapshot it is built and tested but not yet wired to a user-facing endpoint; the Ask panel uses the tool approach above.
- `backend/app/services/provenance.py` answers "where did this number come from" and reports any link that is not stored as "not recorded", never as zero.

### Machine quantities cannot reach a priced estimate unreviewed

This is the part I care most about, because a wrong quantity becomes a wrong price in front of a client.

- `backend/app/services/drawing/` is the takeoff engine. `cad_extractor.py` reads DWG/DXF, scopes the model to the new-work area before counting anything (whole-model counts were the first real failure), then extracts door/window/fixture counts, areas and wall lengths. `floor_area.py`, `room_areas.py` and `room_schedule.py` measure areas from line-work and room tags; `scale_check.py` checks a PDF sheet's printed scale against its own dimension strings and only reports a scale when enough readings agree; `set_completeness.py` flags drawing sets that are missing storeys.
- `backend/app/services/quantity_gate.py` is the enforcement. Estimate jobs are refused while any quantity they would use is still a machine candidate, and the refusal names those quantities. The accuracy of the path (measured from CAD, read from PDF, or no drawings) is stated up front.
- `backend/app/services/drawing_analysis.py` builds a drawing-analysis pack that a person signs. Rebuilding after a new drawing arrives clears the confirmation only on quantities whose value changed.
- Tests: `tests/test_quantity_gate.py`, `tests/test_drawing_analysis_gate.py`, `tests/test_cad_takeoff.py` (synthetic DXF fixtures built by `tests/fixtures/make_takeoff_dxf.py`), `tests/test_golden_real_drawing.py`, `tests/test_takeoff_aggregation.py`. The design and the validation record are in `docs/cad-takeoff-engine.md` and `docs/keystone-drawing-analysis-gate-plan.md`.

### Prompt versions need human approval

`backend/app/services/skill_engine.py` and `backend/app/models/skill.py` keep a registry of prompt versions. Every run is logged against the version that produced it, and when someone corrects an AI output the JSON diff is stored as correction signals (`record_corrections`). `sync_active_prompt` fingerprints the prompt that is actually in code; if it has drifted from the approved version it registers a draft candidate and never promotes it automatically. The only way to activate a version is `POST /skills/{id}/versions/{vid}/approve` in `backend/app/routers/skills.py`, which is superadmin-only and records who approved it and why.

AI edits to documents follow the same idea. `backend/app/routers/ai_edits.py` applies a model-proposed change set only after a person accepts it, and re-checks each change's stored `before` value against the current document so an edit made in the meantime is not overwritten.

### Evals for model output

`backend/app/evals/` runs golden cases through kind-specific scorers (recall on set fields, truncation as a hard fail) and reports pass rate, cost and p50/p95 latency, with a non-zero exit when a case fails. It runs offline with a mock by default (`python -m app.evals`) or against the real model with `--live`. `tests/test_evals.py` covers it in CI.

### Tests and CI that fail loudly

- `.github/workflows/ci.yml` starts a pgvector Postgres, runs every Alembic migration, then runs the full pytest suite against a separate throwaway database.
- `backend/tests/conftest.py` has three guards in front of the destructive DB fixtures: `TEST_DATABASE_URL` must be set explicitly (no fallback to `DATABASE_URL`), the database name must look like a test database, and the target must be empty of project data. The docstring explains the production data loss that led to them. `REQUIRE_DB_TESTS=1` turns a missing test database into an error, added after CI had been reporting green while 141 DB tests were silently skipped.
- Role and isolation tests probe the API as each role rather than checking what the UI hides: `tests/test_crew_sees_no_money.py`, `tests/test_project_isolation.py`, `tests/test_projects_access.py`, `tests/test_superadmin_only_surfaces.py`.
- Money: `tests/test_estimate_golden_arithmetic.py`, `tests/test_money.py`, `tests/test_invoice_idempotency.py`, `tests/test_estimate_totals_contingency.py`.
- Frontend: `frontend/scripts/check-*.mjs` run as `prebuild`, so `next build` fails if a server component imports a client-only module, a page shows a raw error body, or one of several pure helpers (scale selection, bbox transforms, estimate email defaults) regresses.

### Other parts of the product

Client and CRM records with matching and de-duplication (`services/clients.py`); estimate versions with activation and a costed approval step before AI documents are regenerated (`services/regeneration.py`); contracts, schedule of values, billing plans and holdback (`services/billing_plan.py`, `services/sov_progress.py`); schedule re-planning from site reports (`services/replan.py`, `services/schedule_replan.py`); submittal review against the governing drawings (`services/submittal_review.py`); inbound email filed to the right project (`routers/inbound_email.py`); and the client proposal portal with typed-name acceptance (`services/proposal_portal.py`).

## Running it

Backend:

```bash
docker compose -f docker-compose.dev.yml up -d      # Postgres on localhost:5434
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # fill in keys as needed
alembic upgrade head
uvicorn app.main:app --reload --port 8004
```

The dev compose file uses plain Postgres; the RAG migration needs the pgvector extension, so use the `pgvector/pgvector:pg16` image if you run every migration (CI does). WeasyPrint needs Pango and Cairo installed on the machine.

Tests without a database (DB-backed tests skip):

```bash
cd backend && ANTHROPIC_API_KEY=dummy pytest -q
```

Frontend:

```bash
cd frontend
cp .env.local.example .env.local
npm ci
npm run typecheck
npm run build
```

## Notes

- The Anthropic client is dependency-injected in the worker and evals, so no test calls a real model.
- `pymupdf` is AGPL; the production system is internal-use only.
- Some data needed for full behaviour is not included: real drawings, the supplier price list loaded by `scripts/load_renovo_catalogue.py`, and production configuration.
