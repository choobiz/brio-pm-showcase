"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";

import { RateUpdatesStep } from "@/components/RateUpdatesStep";
import { SkillFeedbackModal } from "@/components/SkillFeedbackModal";
import { previewRateWriteback, type RateDecision, type RateProposal } from "@/lib/rates";
import {
  EstimateCategory,
  EstimateItem,
  EstimatePayload,
  recomputePayload,
} from "@/lib/estimate-rollup";
import { fmtMoney } from "@/lib/fmt";
import {
  saveEditedEstimateAction,
  updateExistingEstimateAction,
} from "../../actions";

const fmtPct = (n: number) => `${n.toFixed(2)}%`;

/**
 * Auto-reflow a line item when the user changes one field. Keeps
 * qty × unitCost ≈ labor + materials so the rollup matches what the
 * user sees. Specific rules:
 *   - editing qty=0 zeroes the whole line (labor + materials)
 *   - editing qty>0 (with prior qty>0) scales labor + materials by the
 *     qty ratio and recomputes unitCost = total / qty
 *   - editing unitCost scales labor + materials to match qty × unitCost,
 *     preserving the existing labor:materials split (or 50/50 if both zero)
 *   - editing labor or materials recomputes unitCost = total / qty
 */
function reflowItem(
  prev: EstimateItem,
  field: "qty" | "unitCost" | "labor" | "materials" | "name",
  raw: string,
): EstimateItem {
  if (field === "name") return { ...prev, name: raw };

  const next: EstimateItem = { ...prev };
  const parsed = raw === "" ? 0 : Number(raw);
  const oldQty = Number(prev.qty) || 0;
  const oldLabor = Number(prev.labor) || 0;
  const oldMaterials = Number(prev.materials) || 0;
  const oldUnitCost = Number(prev.unitCost) || 0;

  if (field === "qty") {
    next.qty = raw === "" ? undefined : parsed;
    if (parsed === 0) {
      next.labor = 0;
      next.materials = 0;
      // keep unitCost as the per-unit rate
    } else if (oldQty > 0) {
      const r = parsed / oldQty;
      next.labor = Math.round(oldLabor * r);
      next.materials = Math.round(oldMaterials * r);
      const total = (next.labor || 0) + (next.materials || 0);
      next.unitCost = Math.round((total / parsed) * 100) / 100;
    } else {
      // No prior qty — assume unitCost is the per-unit rate
      const total = parsed * oldUnitCost;
      next.labor = 0;
      next.materials = Math.round(total);
    }
  } else if (field === "unitCost") {
    next.unitCost = parsed;
    const total = oldQty * parsed;
    const oldTotal = oldLabor + oldMaterials;
    if (oldTotal > 0) {
      const r = total / oldTotal;
      next.labor = Math.round(oldLabor * r);
      next.materials = Math.round(oldMaterials * r);
    } else {
      next.labor = Math.round(total / 2);
      next.materials = Math.round(total - (next.labor || 0));
    }
  } else if (field === "labor" || field === "materials") {
    next[field] = parsed;
    const labor = field === "labor" ? parsed : oldLabor;
    const materials = field === "materials" ? parsed : oldMaterials;
    const total = labor + materials;
    next.unitCost = oldQty > 0 ? Math.round((total / oldQty) * 100) / 100 : oldUnitCost;
  }

  return next;
}

/**
 * Auto-suggest a new reference by bumping the trailing integer of the parent
 * reference. Preserves any prefix and the original zero-padding width.
 *   "EST-10058"              → "EST-10059"
 *   "EST-PROJB-MASTER-LOW-009" → "EST-PROJB-MASTER-LOW-010"
 *   "anything-no-number"     → "" (force user to type one)
 */
function suggestNextReference(parentRef: string | undefined | null): string {
  if (!parentRef) return "";
  const m = parentRef.match(/^(.*?)(\d+)$/);
  if (!m) return "";
  const [, prefix, numStr] = m;
  const next = String(Number(numStr) + 1).padStart(numStr.length, "0");
  return prefix + next;
}

const inputCls =
  "border border-slate-300 rounded px-2 py-1 text-sm w-full focus:outline-none focus:ring-1 focus:ring-blue-500";

const numInputCls = inputCls + " text-right tabular-nums";

export function EstimateEditor({
  projectId,
  parentVersionId,
  initialPayload,
}: {
  projectId: string;
  parentVersionId: string;
  initialPayload: EstimatePayload;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  // S1.5 — post-save feedback modal. On close we run navigateTo so the
  // user lands on the new/updated version. Modal is silent if there's no
  // originating AI run linked to the estimate version (manual / paste).
  const [feedback, setFeedback] = useState<{
    marker: string;
    value: string;
    output: Record<string, unknown>;
    navigateTo: string;
  } | null>(null);

  // Whole-payload local state. We mutate it through targeted setters below,
  // then derive computed rollups via useMemo so the UI is always coherent.
  const [payload, setPayload] = useState<EstimatePayload>(initialPayload);
  // Default to the next reference (parent + 1) instead of reusing the parent's.
  // User can still override before saving.
  const parentRef = initialPayload.reference || "";
  const [reference, setReference] = useState<string>(
    suggestNextReference(parentRef),
  );
  const collidesWithParent = reference.trim() === parentRef.trim();

  const computed = useMemo(() => recomputePayload(payload), [payload]);

  // ── Mutation helpers ────────────────────────────────────────────────
  function editItem(
    catIdx: number,
    itemIdx: number,
    field: "qty" | "unitCost" | "labor" | "materials" | "name",
    raw: string,
  ) {
    setPayload((p) => {
      const categories = [...p.categories];
      const items = [...(categories[catIdx].items ?? [])];
      items[itemIdx] = reflowItem(items[itemIdx], field, raw);
      categories[catIdx] = { ...categories[catIdx], items };
      return { ...p, categories };
    });
  }

  // Phase 3b.2 — add a new blank line item to a category. Inserts BEFORE
  // the contingency line so the auto-generated row stays at the bottom.
  function addItem(catIdx: number) {
    setPayload((p) => {
      const categories = [...p.categories];
      const items = [...(categories[catIdx].items ?? [])];
      const contIdx = items.findIndex((i) => i.contingency);
      const blank: EstimateItem = {
        name: "",
        qty: 1,
        unitCost: 0,
        labor: 0,
        materials: 0,
        laborHours: 0,
      };
      if (contIdx >= 0) items.splice(contIdx, 0, blank);
      else items.push(blank);
      categories[catIdx] = { ...categories[catIdx], items };
      return { ...p, categories };
    });
  }

  // Phase 3b.2 — remove a line item. Contingency rows can't be removed
  // here (they regenerate on save), but the UI hides the delete button
  // on those rows anyway as a soft guard.
  function removeItem(catIdx: number, itemIdx: number) {
    setPayload((p) => {
      const categories = [...p.categories];
      const items = [...(categories[catIdx].items ?? [])];
      if (items[itemIdx]?.contingency) return p;
      items.splice(itemIdx, 1);
      categories[catIdx] = { ...categories[catIdx], items };
      return { ...p, categories };
    });
  }

  // Phase 3b.2 — edit category metadata (name, contingency %, description).
  function editCategory(catIdx: number, patch: Partial<EstimateCategory>) {
    setPayload((p) => {
      const categories = [...p.categories];
      categories[catIdx] = { ...categories[catIdx], ...patch };
      return { ...p, categories };
    });
  }

  // Remove an entire category (and all its line items). Rollups recompute.
  function removeCategory(catIdx: number) {
    setPayload((p) => ({
      ...p,
      categories: p.categories.filter((_, i) => i !== catIdx),
    }));
  }

  function setOverhead(n: number) {
    setPayload((p) => ({ ...p, overhead: n }));
  }
  function setProfit(n: number) {
    setPayload((p) => ({ ...p, profit: n }));
  }
  function setNotes(s: string) {
    setPayload((p) => ({ ...p, notes: s }));
  }

  // Client discussion points (shown in the package "What We Discussed" section).
  function editPoint(i: number, field: "title" | "body", value: string) {
    setPayload((p) => {
      const pts = [...(p.discussionPoints ?? [])];
      pts[i] = { ...pts[i], [field]: value };
      return { ...p, discussionPoints: pts };
    });
  }
  function addPoint() {
    setPayload((p) => ({ ...p, discussionPoints: [...(p.discussionPoints ?? []), { title: "", body: "" }] }));
  }
  function removePoint(i: number) {
    setPayload((p) => ({ ...p, discussionPoints: (p.discussionPoints ?? []).filter((_, j) => j !== i) }));
  }

  // Reformat inline notes ("… (1) … (2) …") to one item per line for readability.
  function formatNotesOnePerLine(s: string): string {
    if (!s) return s;
    return s
      .replace(/\s*\((\d+)\)\s*/g, "\n($1) ")
      .replace(/\s*(v\d{2,})/gi, "\n$1")
      .replace(/\n{3,}/g, "\n\n")
      .replace(/^\n+/, "")
      .trim();
  }

  // Phase 3 save step: a fix to a linked line also updates the rate library.
  // Ask the server which rates this save would change; with none, save straight away.
  const [rateStep, setRateStep] = useState<{
    proposals: RateProposal[];
    label: string;
    run: (d: RateDecision[]) => void;
  } | null>(null);

  async function withRateStep(document: Record<string, unknown>, label: string, run: (d: RateDecision[]) => void) {
    const { proposals } = await previewRateWriteback({ document });
    if (proposals.length === 0) run([]);
    else setRateStep({ proposals, label, run });
  }

  // Two save modes: create a new immutable version, OR update this row in place.
  function onSaveAsNew() {
    setError(null);
    if (!reference.trim()) {
      setError("Reference cannot be empty (e.g., EST-10059)");
      return;
    }
    if (collidesWithParent) {
      setError(
        `New version reference (${reference}) is the same as the version you're editing. Bump it (e.g., ${suggestNextReference(parentRef)}) so the audit trail is clean.`,
      );
      return;
    }
    const recomputed = recomputePayload({ ...payload, reference });
    const doc = recomputed.payload as unknown as Record<string, unknown>;
    const save = (rate_updates: RateDecision[]) => startTransition(async () => {
      try {
        setRateStep(null);
        const result = await saveEditedEstimateAction(
          projectId,
          parentVersionId,
          doc,
          {
            notes: payload.notes,
            overhead_pct: Number(payload.overhead) || 0,
            profit_pct: Number(payload.profit) || 0,
            reference,
            rate_updates,
          },
        );
        // S1.5 — open the feedback modal. Navigation is deferred until
        // the user closes it (skip / submit / no-AI-run-found all close).
        setFeedback({
          marker: "estimate_version_id",
          value: result.id,
          output: recomputed.payload as unknown as Record<string, unknown>,
          navigateTo: `/projects/${projectId}/estimate/${result.id}`,
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : "Save failed");
      }
    });
    withRateStep(doc, "Save as new version", save).catch((e) =>
      setError(e instanceof Error ? e.message : "Could not check the rate library"),
    );
  }

  function onUpdateInPlace() {
    setError(null);
    // Reference defaults to parent ref so user doesn't need to bump it for an
    // in-place update. Whatever's in the field at save time wins.
    const refToUse = reference.trim() || parentRef;
    const recomputed = recomputePayload({ ...payload, reference: refToUse });
    const doc = recomputed.payload as unknown as Record<string, unknown>;
    const save = (rate_updates: RateDecision[]) => startTransition(async () => {
      try {
        setRateStep(null);
        await updateExistingEstimateAction(
          projectId,
          parentVersionId,
          doc,
          {
            notes: payload.notes,
            overhead_pct: Number(payload.overhead) || 0,
            profit_pct: Number(payload.profit) || 0,
            reference: refToUse,
            rate_updates,
          },
        );
        // S1.5 — feedback modal, then navigate.
        setFeedback({
          marker: "estimate_version_id",
          value: parentVersionId,
          output: recomputed.payload as unknown as Record<string, unknown>,
          navigateTo: `/projects/${projectId}/estimate/${parentVersionId}`,
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : "Update failed");
      }
    });
    withRateStep(doc, "Save estimate", save).catch((e) =>
      setError(e instanceof Error ? e.message : "Could not check the rate library"),
    );
  }

  function onCancel() {
    router.push(
      `/projects/${projectId}/estimate/${parentVersionId}`,
    );
  }

  // ── Render ──────────────────────────────────────────────────────────
  return (
    <div className="space-y-5">
      <header className="sticky top-0 z-10 -mx-4 px-4 py-3 bg-white border-b border-slate-200 flex flex-wrap gap-3 items-end">
        <div className="flex-1 min-w-[280px]">
          <h1 className="text-xl font-semibold">Editing estimate</h1>
          <p className="text-sm text-slate-600 mt-0.5">
            Save creates a new immutable version and marks it active. The
            original {initialPayload.reference} is preserved.
          </p>
        </div>
        <div>
          <label className="block text-[10px] uppercase tracking-wide font-semibold text-slate-500">
            New version reference
          </label>
          <input
            type="text"
            value={reference}
            onChange={(e) => setReference(e.target.value)}
            placeholder={suggestNextReference(parentRef) || "EST-10059"}
            className={
              inputCls +
              ` w-44 mt-0.5 ${collidesWithParent ? "border-red-400 ring-1 ring-red-200" : ""}`
            }
          />
          {collidesWithParent && (
            <div className="text-[10px] text-red-700 mt-0.5">
              same as parent — bump it
            </div>
          )}
          {!collidesWithParent && parentRef && (
            <div className="text-[10px] text-slate-500 mt-0.5">
              parent: {parentRef}
            </div>
          )}
        </div>
        <div>
          <label className="block text-[10px] uppercase tracking-wide font-semibold text-slate-500">
            Overhead %
          </label>
          <input
            type="number"
            step="0.01"
            value={payload.overhead ?? 0}
            onChange={(e) => setOverhead(Number(e.target.value))}
            className={numInputCls + " w-24 mt-0.5"}
          />
        </div>
        <div>
          <label className="block text-[10px] uppercase tracking-wide font-semibold text-slate-500">
            Profit %
          </label>
          <input
            type="number"
            step="0.01"
            value={payload.profit ?? 0}
            onChange={(e) => setProfit(Number(e.target.value))}
            className={numInputCls + " w-24 mt-0.5"}
          />
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={pending}
            className="px-3 py-1.5 rounded border border-slate-300 text-sm hover:bg-slate-50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onUpdateInPlace}
            disabled={pending}
            title={`Overwrite ${parentRef} in place (no new version). Use while iterating.`}
            className="px-3 py-1.5 rounded border border-slate-300 text-sm font-medium hover:bg-slate-100 disabled:opacity-50"
          >
            {pending ? "Saving…" : `Update ${parentRef}`}
          </button>
          <button
            type="button"
            onClick={onSaveAsNew}
            disabled={pending}
            title="Create a new immutable version, marked active. Use when iteration is done."
            className="px-3 py-1.5 rounded bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {pending ? "Saving…" : "Save as new version"}
          </button>
        </div>
        {error && (
          <div className="w-full text-sm text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1">
            {error}
          </div>
        )}
      </header>

      {/* Estimate-level rollup pinned to top */}
      <section className="rounded border border-blue-200 bg-blue-50/40 p-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        <div>
          <div className="text-[10px] uppercase text-slate-500">Direct + Cont</div>
          <div className="text-lg font-semibold tabular-nums">
            {fmtMoney(computed.baseDirectCont + computed.optDirectCont)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-slate-500">
            Markup — OH {fmtPct(Number(payload.overhead) || 0)} + Profit {fmtPct(Number(payload.profit) || 0)}
          </div>
          <div className="text-lg font-semibold tabular-nums">
            {fmtMoney(computed.combinedPretax - computed.baseDirectCont - computed.optDirectCont)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-slate-500">Pretax</div>
          <div className="text-lg font-semibold tabular-nums text-blue-800">
            {fmtMoney(computed.combinedPretax)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-slate-500">
            Total Payable (incl. 5% GST)
          </div>
          <div className="text-lg font-semibold tabular-nums">
            {fmtMoney(computed.combinedPretax * 1.05)}
          </div>
        </div>
      </section>

      {/* Notes (free text) */}
      <section className="space-y-1">
        <div className="flex items-center justify-between">
          <label className="text-[10px] uppercase tracking-wide font-semibold text-slate-500">
            Estimate notes
          </label>
          {(payload.notes ?? "").trim().length > 0 && (
            <button
              type="button"
              onClick={() => setNotes(formatNotesOnePerLine(payload.notes ?? ""))}
              className="text-[11px] font-medium text-blue-600 hover:underline"
              title="Put each numbered note on its own line"
            >
              Format · one per line
            </button>
          )}
        </div>
        <textarea
          value={payload.notes ?? ""}
          onChange={(e) => setNotes(e.target.value)}
          rows={10}
          className={inputCls + " min-h-[200px] resize-y whitespace-pre-wrap font-mono text-xs leading-relaxed"}
        />
        <p className="text-[10px] text-slate-400">
          Tip: press Enter to put each note on its own line, or use “Format · one per line”.
        </p>
      </section>

      {/* Client Discussion Notes — shown in the package "What We Discussed" section */}
      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <label className="text-[10px] uppercase tracking-wide font-semibold text-slate-500">
            Client Discussion Notes
          </label>
          <button type="button" onClick={addPoint} className="text-[11px] font-medium text-blue-600 hover:underline">
            + Add point
          </button>
        </div>
        <p className="text-[10px] text-slate-400">
          Shown in the client package’s “What We Discussed” section. In the stormwater point,{" "}
          <code>{"{sw}"}</code> / <code>{"{site}"}</code> / <code>{"{total}"}</code> auto-fill from the estimate.
        </p>
        <div className="space-y-2">
          {(payload.discussionPoints ?? []).map((pt, i) => (
            <div key={i} className="space-y-1 rounded border border-slate-200 p-2">
              <div className="flex items-center gap-2">
                <span className="w-4 text-[10px] text-slate-400">{i + 1}</span>
                <input
                  type="text"
                  value={pt.title ?? ""}
                  onChange={(e) => editPoint(i, "title", e.target.value)}
                  placeholder="Title"
                  className={inputCls + " flex-1 text-sm font-medium"}
                />
                <button
                  type="button"
                  onClick={() => removePoint(i)}
                  className="px-1 text-slate-400 hover:text-red-600"
                  title="Remove point"
                >
                  ✕
                </button>
              </div>
              <textarea
                value={pt.body ?? ""}
                onChange={(e) => editPoint(i, "body", e.target.value)}
                rows={4}
                placeholder="What was discussed…"
                className={inputCls + " w-full resize-y text-xs leading-relaxed"}
              />
            </div>
          ))}
          {(payload.discussionPoints ?? []).length === 0 && (
            <p className="text-xs italic text-slate-400">
              No discussion points. Add one to include it in the client package.
            </p>
          )}
        </div>
      </section>

      {/* Categories + line items */}
      {computed.payload.categories.map((cat, ci) => (
        <CategoryEditor
          key={ci}
          cat={cat}
          onEditItem={(itemIdx, field, raw) => editItem(ci, itemIdx, field, raw)}
          onAddItem={() => addItem(ci)}
          onRemoveItem={(itemIdx) => removeItem(ci, itemIdx)}
          onEditCategory={(patch) => editCategory(ci, patch)}
          onRemoveCategory={() => removeCategory(ci)}
        />
      ))}

      {rateStep && (
        <RateUpdatesStep
          proposals={rateStep.proposals}
          saveLabel={rateStep.label}
          busy={pending}
          onCancel={() => setRateStep(null)}
          onConfirm={rateStep.run}
        />
      )}

      {feedback && (
        <SkillFeedbackModal
          skillId="estimate_generator"
          artifactMarker={feedback.marker}
          artifactValue={feedback.value}
          acceptedOutput={feedback.output}
          onClose={() => {
            const target = feedback.navigateTo;
            setFeedback(null);
            router.push(target);
            // Bust the client-side Router Cache so the destination re-fetches the
            // just-saved payload from the server — without this, an in-place update
            // lands on a prefetched (stale) render of the estimate view.
            router.refresh();
          }}
        />
      )}
    </div>
  );
}

function CategoryEditor({
  cat,
  onEditItem,
  onAddItem,
  onRemoveItem,
  onEditCategory,
  onRemoveCategory,
}: {
  cat: EstimateCategory;
  onEditItem: (
    itemIdx: number,
    field: "qty" | "unitCost" | "labor" | "materials" | "name",
    raw: string,
  ) => void;
  onAddItem: () => void;
  onRemoveItem: (itemIdx: number) => void;
  onEditCategory: (patch: Partial<EstimateCategory>) => void;
  onRemoveCategory: () => void;
}) {
  return (
    <section className="border border-slate-200 rounded">
      <header
        className={`px-3 py-2 ${cat.optional ? "bg-amber-50" : "bg-slate-50"}`}
      >
        {/* Row 1: editable name + optional badge + live category totals */}
        <div className="flex justify-between items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2 flex-1 min-w-[260px]">
            <input
              type="text"
              value={cat.name}
              onChange={(e) => onEditCategory({ name: e.target.value })}
              className={inputCls + " text-sm font-medium"}
            />
            {cat.optional && (
              <span className="text-[10px] uppercase tracking-wide text-amber-700 bg-amber-100 px-1.5 py-0.5 rounded whitespace-nowrap">
                Optional
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600 tabular-nums">
            <span className="whitespace-nowrap">
              Raw: <strong>{fmtMoney(cat.subtotalRaw ?? 0)}</strong>
            </span>
            <span className="flex items-center gap-1 whitespace-nowrap">
              Cont:
              <input
                type="number"
                step="0.5"
                min="0"
                max="100"
                value={cat.contingencyPct ?? 5}
                onChange={(e) =>
                  onEditCategory({ contingencyPct: Number(e.target.value) })
                }
                className={numInputCls + " w-14 text-xs py-0.5"}
              />
              %
              <span className="text-slate-500">
                ({fmtMoney(cat.contingencyAmount ?? 0)})
              </span>
            </span>
            <span className="whitespace-nowrap">
              Total: <strong>{fmtMoney(cat.total ?? 0)}</strong>
            </span>
          </div>
          <button
            type="button"
            onClick={() => {
              if (
                window.confirm(
                  `Remove the entire "${cat.name || "Untitled"}" category and all its line items?`,
                )
              )
                onRemoveCategory();
            }}
            title="Remove this entire category"
            className="shrink-0 text-[11px] font-medium text-slate-400 hover:text-red-600 whitespace-nowrap px-2 py-1 rounded border border-slate-200 hover:border-red-200 hover:bg-red-50"
          >
            ✕ Remove category
          </button>
        </div>
      </header>

      {/* A category can carry its cost in subtotalRaw with NO line items. Every
          estimate recovered from an issued PDF after the 2026-08-31 data loss
          looks like this: the scope survived as uncosted items, and the money
          sits in one "Lump-sum price as issued" category with nothing under it.
          The editor only ever exposed per-item cost fields, so that figure was
          the one number on the estimate nobody could change. recomputeCategory
          already preserves it, so this is a missing input, not a missing rule. */}
      {(cat.items ?? []).length === 0 && (
        <div className="flex items-center gap-2 px-3 py-2 border-t border-slate-100 bg-amber-50/40">
          <label className="text-xs text-slate-600">
            Lump-sum amount for this category
          </label>
          <input
            type="number"
            step="0.01"
            min="0"
            value={cat.subtotalRaw ?? 0}
            onChange={(e) =>
              onEditCategory({ subtotalRaw: Number(e.target.value) || 0 })
            }
            className={inputCls + " w-36 text-right text-xs"}
          />
          <span className="text-[11px] text-slate-500">
            no line items, so the total comes from this figure
          </span>
        </div>
      )}

      <table className="w-full text-xs">
        <thead className="text-slate-600 bg-slate-50/50">
          <tr>
            <th className="text-left px-2 py-1 font-medium">Item name</th>
            <th className="text-right px-2 py-1 font-medium w-16">Qty</th>
            <th className="text-right px-2 py-1 font-medium w-20">Unit $</th>
            <th className="text-right px-2 py-1 font-medium w-24">Labor</th>
            <th className="text-right px-2 py-1 font-medium w-24">Materials</th>
            <th className="text-right px-2 py-1 font-medium w-24">Item Total</th>
            <th className="px-2 py-1 w-8"></th>
          </tr>
        </thead>
        <tbody>
          {(cat.items ?? []).map((item, ii) => {
            const isCont = Boolean(item.contingency);
            const itemTotal = (Number(item.labor) || 0) + (Number(item.materials) || 0);
            return (
              <tr
                key={ii}
                className={`border-t border-slate-100 ${
                  isCont ? "bg-slate-50 text-slate-500 italic" : ""
                }`}
              >
                <td className="px-2 py-1">
                  {isCont ? (
                    <span>{item.name}</span>
                  ) : (
                    <input
                      type="text"
                      value={item.name}
                      onChange={(e) => onEditItem(ii, "name", e.target.value)}
                      className={inputCls + " text-xs"}
                    />
                  )}
                </td>
                <td className="px-2 py-1">
                  {isCont ? (
                    <span className="block text-right">{String(item.qty ?? "")}</span>
                  ) : (
                    <input
                      type="text"
                      value={String(item.qty ?? "")}
                      onChange={(e) => onEditItem(ii, "qty", e.target.value)}
                      className={numInputCls + " text-xs"}
                    />
                  )}
                </td>
                <td className="px-2 py-1">
                  {isCont ? (
                    <span className="block text-right">{fmtMoney(item.unitCost ?? 0)}</span>
                  ) : (
                    <input
                      type="number"
                      step="0.01"
                      value={item.unitCost ?? 0}
                      onChange={(e) => onEditItem(ii, "unitCost", e.target.value)}
                      className={numInputCls + " text-xs"}
                    />
                  )}
                </td>
                <td className="px-2 py-1">
                  {isCont ? (
                    <span className="block text-right">{fmtMoney(item.labor ?? 0)}</span>
                  ) : (
                    <input
                      type="number"
                      step="1"
                      value={item.labor ?? 0}
                      onChange={(e) => onEditItem(ii, "labor", e.target.value)}
                      className={numInputCls + " text-xs"}
                    />
                  )}
                </td>
                <td className="px-2 py-1">
                  {isCont ? (
                    <span className="block text-right">{fmtMoney(item.materials ?? 0)}</span>
                  ) : (
                    <input
                      type="number"
                      step="1"
                      value={item.materials ?? 0}
                      onChange={(e) => onEditItem(ii, "materials", e.target.value)}
                      className={numInputCls + " text-xs"}
                    />
                  )}
                </td>
                <td className="px-2 py-1 text-right tabular-nums font-medium">
                  {fmtMoney(itemTotal)}
                </td>
                <td className="px-1 py-1 text-center">
                  {isCont ? null : (
                    <button
                      type="button"
                      onClick={() => onRemoveItem(ii)}
                      title="Delete this line"
                      className="text-slate-400 hover:text-red-600 text-base leading-none w-6 h-6 rounded hover:bg-red-50"
                    >
                      ×
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
          <tr>
            <td colSpan={7} className="px-2 py-1.5 border-t border-slate-100">
              <button
                type="button"
                onClick={onAddItem}
                className="text-xs text-blue-600 hover:text-blue-800 font-medium"
              >
                + Add line item
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  );
}
