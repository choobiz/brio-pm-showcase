"use client";

import { useEffect, useState } from "react";

import type { CostEstimate } from "@/lib/prep";
import { errorMessage } from "@/lib/workspaceApi";

/**
 * The approximate cost of a step, shown BEFORE it runs, and the person's
 * approval (Amir, 2026-10-13). The approved amount travels with the request
 * and the server stops at it.
 *
 * On the Mac the step is free (Claude Code on Amir's subscription); the
 * approval only covers the server running it if the Mac doesn't. "Mac only"
 * approves $0: the step waits for the Mac and never charges.
 */
export function CostApproval({
  load,
  action,
  disabled,
  busy,
  onApprove,
}: {
  load: () => Promise<CostEstimate>;
  action: string;
  disabled?: boolean;
  busy?: boolean;
  onApprove: (usd: number) => void;
}) {
  const [est, setEst] = useState<CostEstimate | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setEst(null);
    load().then((e) => live && setEst(e)).catch((e) => live && setErr(errorMessage(e)));
    return () => {
      live = false;
    };
  }, [load]);

  if (err) return <p className="m-0 text-sm text-red-700">Couldn&rsquo;t estimate the cost: {err}</p>;
  if (!est) return <p className="m-0 text-sm text-slate-500">Estimating the cost…</p>;

  const usd = `$${est.server_usd.toFixed(2)}`;
  return (
    <div className="space-y-2 rounded-md border border-slate-300 bg-slate-50 p-3 text-sm">
      <p className="m-0 text-slate-800">
        {est.mac_available ? (
          <>Your Mac is on, so this should be <b>free</b>. If the server has to run it instead: <b>up to {usd}</b>.</>
        ) : (
          <>Your Mac is off, so the server will run this: <b>up to {usd}</b>.</>
        )}
      </p>
      <details className="text-xs text-slate-600">
        <summary className="cursor-pointer">How this is worked out</summary>
        <ul className="m-0 mt-1 list-disc pl-5">
          {est.lines.map((l, i) => (
            <li key={i}>
              {l.label}: ${l.usd.toFixed(2)} <span className="text-slate-500">({l.how})</span>
            </li>
          ))}
        </ul>
        <p className="m-0 mt-1">The server stops if it would spend more than you approve.</p>
      </details>
      <div className="flex flex-wrap justify-end gap-2">
        {est.mac_available && (
          <button type="button" disabled={disabled || busy} onClick={() => onApprove(0)}
            className="rounded-md border border-slate-400 px-3 py-1.5 text-slate-800 disabled:opacity-40">
            {action} on my Mac only ($0)
          </button>
        )}
        <button type="button" disabled={disabled || busy} onClick={() => onApprove(est.server_usd)}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-white disabled:opacity-40">
          {busy ? "Working…" : `Approve up to ${usd} and ${action.toLowerCase()}`}
        </button>
      </div>
    </div>
  );
}
