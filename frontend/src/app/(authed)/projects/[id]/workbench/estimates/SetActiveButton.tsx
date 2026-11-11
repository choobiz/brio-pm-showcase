"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import type { RegenerationPlan } from "@/lib/workbench";
import { getRegenerationPlanAction, activateWithRegenerationAction } from "./actions";

/**
 * Phase C — activating an estimate version is now a governed action: it
 * regenerates the downstream documents so they stay consistent with the
 * estimate (the source of truth). Clicking opens a notice modal with a
 * per-artifact token-cost breakdown; management approves before any AI spend.
 */
type Phase = "idle" | "loading" | "confirm" | "activating" | "done";

const money = (n: number) => `$${n.toFixed(2)}`;

export function SetActiveButton({
  projectId,
  versionId,
  label = "Set Active",
}: {
  projectId: string;
  versionId: string;
  /** The Estimate page calls this "Keep". */
  label?: string;
}) {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("idle");
  const [plan, setPlan] = useState<RegenerationPlan | null>(null);
  const [result, setResult] = useState<{ count: number; cost: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const open = async () => {
    setError(null);
    setPhase("loading");
    try {
      const p = await getRegenerationPlanAction(projectId, versionId);
      setPlan(p);
      setPhase("confirm");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load plan");
      setPhase("idle");
    }
  };

  const approve = async () => {
    setPhase("activating");
    setError(null);
    try {
      const res = await activateWithRegenerationAction(projectId, versionId);
      setResult({ count: res.jobs.length, cost: res.estimated_cost_usd });
      setPhase("done");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Activation failed");
      setPhase("confirm");
    }
  };

  const close = () => {
    setPhase("idle");
    setPlan(null);
    setResult(null);
    setError(null);
  };

  return (
    <>
      <button
        type="button"
        disabled={phase === "loading"}
        onClick={open}
        className="rounded border border-slate-300 px-2 py-1 text-xs font-medium hover:border-blue-500 hover:text-blue-700 disabled:opacity-50"
      >
        {phase === "loading" ? "…" : label}
      </button>
      {error && phase === "idle" && <span className="ml-2 text-xs text-red-600">{error}</span>}

      {(phase === "confirm" || phase === "activating" || phase === "done") && plan && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={close}>
          <div
            className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            {phase !== "done" ? (
              <>
                <h3 className="text-base font-semibold text-slate-800">Activate &amp; regenerate downstream</h3>
                <p className="mt-1 text-sm text-slate-600">
                  Making this the active estimate will regenerate the documents below so they stay consistent with it.
                  The AI documents consume tokens.
                </p>
                <div className="mt-3 divide-y divide-slate-100 rounded-md border border-slate-200">
                  {plan.artifacts.map((a) => (
                    <div key={a.name} className="flex items-center justify-between px-3 py-2 text-sm">
                      <span className="text-slate-700">
                        {a.name}
                        <span className="ml-2 text-xs text-slate-400">{a.kind === "ai" ? "AI" : "recompute"}</span>
                      </span>
                      <span className={a.cost > 0 ? "font-medium text-slate-700" : "text-slate-400"}>
                        {a.cost > 0 ? `~${money(a.cost)}` : "free"}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="mt-2 flex items-center justify-between text-sm">
                  <span className="text-slate-500">Estimated token cost</span>
                  <span className="font-semibold text-slate-800">~{money(plan.total_cost_usd)}</span>
                </div>
                {error && <p className="mt-2 rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">{error}</p>}
                <div className="mt-4 flex items-center justify-end gap-2">
                  <button onClick={close} disabled={phase === "activating"} className="rounded px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">
                    Cancel
                  </button>
                  <button
                    onClick={approve}
                    disabled={phase === "activating"}
                    className="rounded-md px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
                    style={{ backgroundColor: "#0068A0" }}
                  >
                    {phase === "activating" ? "Approving…" : "Approve & regenerate"}
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3 className="text-base font-semibold text-slate-800">Activated ✓</h3>
                <p className="mt-2 text-sm text-slate-600">
                  This estimate is now active. {result?.count ?? 0} document{(result?.count ?? 0) === 1 ? "" : "s"} queued for
                  regeneration (~{money(result?.cost ?? 0)}). They&apos;ll process in the background over the next few minutes —
                  refresh each tab to see the updated documents.
                </p>
                <div className="mt-4 flex justify-end">
                  <button onClick={close} className="rounded-md px-3 py-1.5 text-sm font-medium text-white" style={{ backgroundColor: "#0068A0" }}>
                    Done
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
