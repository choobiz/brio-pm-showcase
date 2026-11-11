"use client";

import { useEffect, useState } from "react";

/**
 * Choose which trades the estimate walks.
 *
 * estimate_full runs one AI call per discipline and there are six, so this is
 * the single thing that decides whether a run takes three minutes or eight. A
 * partition wall was paying for a plumbing pass and a mechanical pass with
 * nothing to price.
 *
 * Recommended, never automatic. The recommendation is keyword matching over the
 * scope text and it does not understand negation: a scope reading "no
 * electrical upgrades required" recommends electrical, because the word is
 * there. Shown with its reason so that is obvious and one click to fix. An
 * automatic router would have silently included it — or worse, silently dropped
 * a trade, and nobody checking the total would see the gap.
 */
const LABEL: Record<string, string> = {
  architectural: "Architectural",
  structural: "Structural",
  mechanical: "Mechanical",
  electrical: "Electrical",
  plumbing: "Plumbing",
  gc_rollup: "General conditions",
};

// Measured, not guessed: a 6-discipline run took 452s and a 4-discipline run
// 220s, so roughly 60s per trade plus overhead. Stated as a range.
const SECONDS_PER_DISCIPLINE = 60;

export default function DisciplinePlan({
  plan,
  value,
  onChange,
  disabled,
}: {
  plan?: {
    recommended: string[];
    skipped: string[];
    reasons: Record<string, string[]>;
    basis: "scope" | "none";
  };
  value: string[] | null;
  onChange: (next: string[]) => void;
  disabled?: boolean;
}) {
  const all = [...(plan?.recommended ?? []), ...(plan?.skipped ?? [])];
  const ordered = ["architectural", "structural", "mechanical", "electrical", "plumbing", "gc_rollup"]
    .filter((d) => all.includes(d));

  // Seed from the recommendation once it arrives, then leave the user's choice.
  useEffect(() => {
    if (value === null && plan?.recommended?.length) onChange(plan.recommended);
  }, [plan, value, onChange]);

  const [open, setOpen] = useState(false);
  const chosen = value ?? plan?.recommended ?? [];
  if (!plan || ordered.length === 0) return null;

  const mins = Math.max(1, Math.round((chosen.length * SECONDS_PER_DISCIPLINE) / 60));
  const isDefault =
    chosen.length === (plan.recommended?.length ?? 0) &&
    chosen.every((d) => plan.recommended.includes(d));

  function toggle(d: string) {
    const next = chosen.includes(d) ? chosen.filter((x) => x !== d) : [...chosen, d];
    onChange(next);
  }

  return (
    <div className="mt-2 rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium text-slate-600">Trades to price:</span>
        <span className="text-slate-700">
          {chosen.length} of {ordered.length}
        </span>
        <span className="text-slate-500">· roughly {mins}&ndash;{mins + 2} min</span>
        {isDefault && plan.basis === "scope" && (
          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-800">
            recommended from the scope
          </span>
        )}
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="ml-auto text-[11px] font-medium text-blue-600 hover:underline"
        >
          {open ? "Hide" : "Change"}
        </button>
      </div>

      {chosen.length === 0 && (
        <div className="mt-1 text-amber-700">Pick at least one trade.</div>
      )}

      {open && (
        <div className="mt-2 space-y-1">
          {ordered.map((d) => {
            const why = plan.reasons?.[d]?.[0];
            return (
              <label key={d} className="flex items-start gap-2 text-slate-700">
                <input
                  type="checkbox"
                  checked={chosen.includes(d)}
                  onChange={() => toggle(d)}
                  disabled={disabled}
                  className="mt-0.5"
                />
                <span>
                  {LABEL[d] ?? d}
                  {why ? <span className="text-slate-500"> — {why}</span> : null}
                  {!why && plan.skipped.includes(d) ? (
                    <span className="text-slate-400"> — nothing in the scope points to this trade</span>
                  ) : null}
                </span>
              </label>
            );
          })}
          <p className="pt-1 text-[11px] text-slate-500">
            Each trade is a separate pass, so each one adds about a minute. Dropping a
            trade that is genuinely in the job leaves it out of the price entirely.
          </p>
        </div>
      )}
    </div>
  );
}
