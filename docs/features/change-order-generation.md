# Change orders generated from scope and documents

**Requested:** 2026-09-22, while pricing extra work at 880 Harbour Drive that came up after the
first part was finished.

## The gap

Estimates have a whole generation pipeline. Change orders have none.

| | Generation available |
|---|---|
| Estimates | `estimate_generation`, `estimate_full`, `cad_takeoff`, `scope_analysis` |
| Change orders | none. The router has list, create, patch, submit, void |

A change order today is four typed fields: `title`, `description`, `cost_impact`,
`schedule_impact_days`. The pieces to do better already exist and are simply not connected:
`_merge_scope_inputs` already materialises documents from R2 into content blocks, and
`change_orders` already carries the approval trail an estimate version does not
(`status`, `decided_by_id`, `decided_at`, `owner_note`).

## Why not just revise the estimate

At most one `estimate_versions` row per project is `is_active`, and activating a new one
atomically clears the previous. Generating a new version to price extra work therefore
supersedes the version the original invoices were seeded from. The invoices survive, because
each stores its own `sourceEstimate` reference, but the project's active estimate then
describes the extra work rather than the contract.

The commercial argument is stronger than the data one. On a lump-sum contract the only thing
protecting BRIO in a dispute is a documented, approved change: the client asked, was told the
cost, and agreed before the work was built. A quietly revised estimate destroys that and reads
as though the original price was inflated after the fact.

## Scope of this build

**In:**
1. A new AI job kind, `change_order_generation`.
2. An endpoint that takes a short description of the extra work plus selected `document_ids`,
   and enqueues the job.
3. A prompt that prices **only the extra work described**, never the whole project.
4. Structured output landed as a **draft** change order: title, description, cost impact,
   schedule impact, and a line-item breakdown kept for evidence.
5. A control on the change-orders page to run it and review before saving.

**Out, deliberately:**
- CAD takeoff for change orders. `cad_takeoff` is a separate deterministic pipeline; wiring it
  in doubles the surface for no benefit on a small extra.
- Auto-approval. Generation produces a **draft**. A person submits it. The approval trail is
  the entire point of preferring a change order over an estimate revision.
- Touching the active estimate. A generated change order must never alter `is_active`.

## Design

    POST /projects/{id}/change-orders/generate
      { instruction: "...", document_ids: [...] }
        -> ai_jobs row, kind=change_order_generation, status=queued
        -> worker: _merge_scope_inputs materialises the documents
        -> build_change_order_messages() prices only what the instruction describes
        -> _persist_change_order_from_result() writes a DRAFT change_orders row
        -> job.output carries change_order_id + number

`kind` is a Postgres enum (`ai_job_kind`), so the new value needs a migration. Enum values
cannot be added inside a transaction block in older PostgreSQL, and Alembic wraps migrations in
one, so the migration commits first.

## Risks

- **Pricing something already in contract.** The prompt is told the extra work is beyond an
  agreed scope and must price only what the instruction names. It is still a judgement call
  that a person has to make, which is why the output is a draft.
- **Cost drift.** A generated figure is a proposal. `cost_impact` stays editable.
- **Enum migration on production.** Additive and non-destructive, but it is a schema change on
  the live database and is applied deliberately.
