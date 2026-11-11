"use client";

import { useState } from "react";

import type { RateDecision, RateProposal } from "@/lib/rates";

const money = (n: number | undefined) =>
  n == null ? "" : `$${n.toLocaleString("en-CA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/**
 * The save step after a review fix (Phase 3: Linked Pricing, wireframe B,
 * approved by Amir 2026-10-16). Each fixed line names the library rate it
 * changes, ticked by default; unticking keeps the fix to this job and needs a
 * reason. A priced allowance can be added as a new rate (unticked).
 * The estimate saves either way.
 */
export function RateUpdatesStep({
  proposals,
  saveLabel,
  busy,
  onCancel,
  onConfirm,
}: {
  proposals: RateProposal[];
  saveLabel: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (decisions: RateDecision[]) => void;
}) {
  const [write, setWrite] = useState<Record<string, boolean>>(
    Object.fromEntries(proposals.map((p) => [p.key, p.default])),
  );
  const [reason, setReason] = useState<Record<string, string>>({});
  const fixes = proposals.filter((p) => p.kind !== "new");
  const fresh = proposals.filter((p) => p.kind === "new");
  const missing = fixes.filter((p) => !write[p.key] && !(reason[p.key] ?? "").trim());
  const count = proposals.filter((p) => write[p.key]).length;

  const row = (p: RateProposal) => (
    <li key={p.key} className="py-2">
      <label className="flex items-start gap-2">
        <input
          type="checkbox"
          checked={!!write[p.key]}
          onChange={(e) => setWrite((w) => ({ ...w, [p.key]: e.target.checked }))}
          className="mt-1"
        />
        <span className="flex-1">
          <span className="font-mono text-[12px]">{p.kind === "new" ? p.line : p.code}</span>{" "}
          {p.kind === "new" ? (
            <>
              {money(p.new_rate)} /{p.unit} → add as your rate
            </>
          ) : (
            <>
              {money(p.old_rate)} → <b>{money(p.new_rate)}</b> /{p.unit}
              {p.kind === "labour" ? " (loaded hourly)" : ""}
            </>
          )}
          <span className="block text-[12px] text-slate-500">
            {p.category} · {p.line}
          </span>
        </span>
      </label>
      {p.kind !== "new" && !write[p.key] && (
        <label className="ml-6 mt-1 block text-[12px] text-slate-700">
          This job only, because:{" "}
          <input
            value={reason[p.key] ?? ""}
            onChange={(e) => setReason((r) => ({ ...r, [p.key]: e.target.value }))}
            placeholder="small room, many cuts"
            className="w-64 rounded border border-slate-300 px-2 py-0.5"
          />
        </label>
      )}
    </li>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true">
      <div className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-md bg-white p-5 shadow-lg">
        <h2 className="m-0 text-lg font-semibold">Update the rate library?</h2>
        {fixes.length > 0 && (
          <>
            <p className="mb-1 mt-2 text-[13px] text-slate-600">These fixes also update the rate library:</p>
            <ul className="m-0 list-none divide-y divide-slate-100 p-0 text-sm">{fixes.map(row)}</ul>
          </>
        )}
        {fresh.length > 0 && (
          <>
            <p className="mb-1 mt-3 text-[13px] text-slate-600">New rates from allowances:</p>
            <ul className="m-0 list-none divide-y divide-slate-100 p-0 text-sm">{fresh.map(row)}</ul>
          </>
        )}
        {missing.length > 0 && (
          <p className="mt-2 text-[12px] text-orange-800">
            Give a reason for each rate kept to this job, or tick it.
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="rounded-md border border-slate-400 px-3 py-1.5 text-sm">
            Cancel
          </button>
          <button
            type="button"
            disabled={busy || missing.length > 0}
            onClick={() =>
              onConfirm(
                proposals.map((p) => ({
                  key: p.key,
                  write: !!write[p.key],
                  ...(write[p.key] ? {} : { reason: (reason[p.key] ?? "").trim() }),
                })),
              )
            }
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {saveLabel}
            {count ? ` + ${count} rate${count === 1 ? "" : "s"}` : ""}
          </button>
        </div>
      </div>
    </div>
  );
}
