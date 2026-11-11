# Keystone — Drawing Analysis + Verification Gate before Estimates & Schedules

**Status: PLAN, awaiting Amir's approval. Nothing implemented.**
Date: 2026-08-28

---

## 1. What already exists (verified in code, not assumed)

More of this is built than the ask implies. The pieces:

| Piece | Where | State |
|---|---|---|
| `cad_takeoff` job kind | `models/ai_job.py:18` | live |
| Deterministic CAD takeoff | `services/drawing/cad_extractor.py` | live, called from `workers/ai_worker.py:1006,1058` |
| Auto-branch before an estimate | `_ensure_cad_takeoff`, `ai_worker.py:967` | live, idempotent |
| CAD ↔ PDF macro reconcile | `_reconcile_takeoff` + `services/drawing/reconcile.py` | live, returns `pass`/`flagged` |
| Readiness classifier | `services/estimate_readiness.py` | live — `takeoff_mode` cad / pdf / none |
| Quantity overrides + review API | `models/quantity_override.py`, `routers/quantity_overrides.py` | live |
| PDF drawing analysis + scale override | `routers/drawings.py` | live |

## 2. The gap — three specific defects, each verified

**2.1 The gate is advisory, not blocking.** `ai_worker.py:1150` — a flagged
reconciliation sets `provenance["needs_review"] = True` and then **generates the estimate
anyway**. Nothing stops, nothing waits for a human.

**2.2 Machine candidates are fed to the estimator as human-verified truth.**
`takeoff_to_overrides` writes rows with `source='cad_extract'` and the note *"Reconcile vs
PDF & confirm before estimate."* But `_verified_quantities` (`ai_worker.py:348`) selects
**every** override row for the discipline with no filter on source or confirmation — and
its own docstring calls the result *"the human-verified ground truth from the review UI."*
`QuantityOverride` has **no `confirmed_at` / `confirmed_by` column at all**, so the
distinction cannot currently be made. This is the crux.

**2.3 The schedule never sees the drawings.** `schedule_derive.py` is given only the
estimate's category names and the scope summary. Storey count, floor-to-floor heights,
spans, site access, phasing between buildings — none of it reaches the scheduler, so
durations and sequencing are inferred from trade names alone.

## 3. Proposed workflow

```
Documents ──► STAGE A: Drawing Analysis ──► STAGE B: Verification Gate ──► STAGE C: Estimate
                    (per sheet)              (human, blocking)                    │
                                                                                  ▼
                                                                          STAGE D: Schedule
                                                                    (reads the SAME verified pack)
```

### Stage A — Drawing Analysis (new `drawing_analysis` job kind)

One run per project produces a **Drawing Analysis Pack**, versioned and immutable once
approved. Per CAD sheet:

- **Quantities** — counts and generated size schedules (doors, windows, fixtures), wall LF,
  gross envelope + perimeter, per-room interior areas.
- **Critical data** *(new — this is the part that does not exist today)*: storey count and
  floor-to-floor from section sheets, structural spans, ceiling heights, site constraints
  and setbacks from the site plan, building separation on multi-building sites.
- **Provenance per number** — which sheet, which method, and a confidence band.
- **Self-declared limits** — every warning the extractor already emits, promoted to
  first-class review items rather than log noise.

Anything not measurable is reported as *not measured*, never inferred. The engine already
returns `None` rather than a bounding box; the pack must preserve that.

### Stage B — Verification Gate (blocking)

A review screen per project. For each quantity: the measured value, the sheet it came
from, the CAD↔PDF reconcile verdict, and three actions — **confirm**, **correct** (type
the right number, with a reason), or **exclude**.

Rules:
1. An estimate cannot run until every quantity it will consume is confirmed, corrected or
   explicitly excluded. Not a warning — a **refusal**, with the unresolved list returned.
2. Confirmation is per quantity, recorded with user and timestamp.
3. Re-running the takeoff on the same drawings preserves confirmations whose measured
   value did not change, and **un-confirms** any whose value moved.
4. Uploading a new revision of a drawing un-confirms everything sourced from it.
5. An override the human **corrected** always beats the machine value, including on re-run.

### Stage C — Estimate

`_verified_quantities` filters to confirmed/corrected rows only. Provenance on the
estimate version records who confirmed what, so a number in a client estimate is traceable
to a sheet and a person.

### Stage D — Schedule

`schedule_from_estimate` additionally receives the verified pack's critical data — storeys,
heights, spans, building count, site constraints — so sequencing and durations are grounded
in the drawings rather than in trade names.

## 4. Data-model changes

- `quantity_overrides`: add `status` (`candidate` | `confirmed` | `corrected` | `excluded`),
  `confirmed_by_id`, `confirmed_at`, `machine_value` (what the extractor said, kept beside
  a human correction), `superseded_at`.
- New `drawing_analysis_packs`: project, version, status (`draft` | `approved`), payload,
  approved_by, approved_at, source document ids + content hashes.
- `ai_job_kind`: add `drawing_analysis`.
- Backfill: existing `cad_extract` rows become `status='candidate'` — which means existing
  projects will show as unverified. **That is the correct outcome**, but it changes
  behaviour for live projects and needs Amir's explicit sign-off.

## 5. API

- `POST /projects/{id}/drawing-analysis` — run Stage A
- `GET  /projects/{id}/drawing-analysis` — the pack + per-item review state
- `POST /projects/{id}/drawing-analysis/items/{key}` — confirm / correct / exclude
- `POST /projects/{id}/drawing-analysis/approve` — close the gate
- `POST /estimates/generate` — **409** with the unresolved list when the gate is open

## 6. Sequencing

| Phase | Work | Est. |
|---|---|---|
| 1 | `status` + confirmation columns; `_verified_quantities` filters on them; estimate refuses when the gate is open | 3–4 h |
| 2 | Pack model + `drawing_analysis` job + API | 4–5 h |
| 3 | Review UI (list, confirm/correct/exclude, sheet provenance) | 5–6 h |
| 4 | Critical-data extraction from sections/site plans | 6–8 h, **needs the §7 work first** |
| 5 | Feed critical data into the schedule prompt | 2–3 h |

## 7. The honest blocker

The takeoff is validated on **one** drawing (1234 Example: rooms to +0.4%). Run against
the Maple coordination set on 2026-08-28 it produced 100 windows on a duplex level and,
on two sheets, silently scoped away more than half the building. Details in
`cad-takeoff-engine.md`.

This does not block Phases 1–3 — it is the strongest possible argument **for** the gate.
It does block Phase 4: extracting "critical data" from sheets the engine cannot yet scope
correctly would produce confident wrong inputs to the schedule.

**Recommended order: fix scope detection and layer classification first, then Phase 1.**

## 8. Decisions — answered by Amir, 2026-08-28

1. **Hard block.** The estimate refuses while any quantity it would consume is unresolved.
2. **Estimator and manager roles only** may confirm, correct or exclude.
3. **Both paths are gated, with the confidence stated up front.** The UI must say plainly:
   *an accurate measured takeoff requires CAD (DWG/DXF); without it the estimate falls back
   to a less accurate PDF-derived takeoff.* `estimate_readiness.takeoff_mode` already carries
   cad / pdf / none — surface it as the headline on the gate screen, not a footnote.
4. **No backfill.** Existing `cad_extract` rows are grandfathered as confirmed; the gate
   applies to new extractions only. Accurate from here forward.
