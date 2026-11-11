"use client";

import { useCallback, useEffect, useState } from "react";

import { listImports, undoImport, type PriceImport } from "@/lib/rates";

const when = (d: string | null) =>
  d ? new Date(d).toLocaleString("en-CA", { dateStyle: "medium", timeStyle: "short" }) : "";

/** Every price-sheet import, newest first; the latest applied one can be undone. */
export function ImportsTab({ onError, refreshKey, onChanged }: { onError: (e: string) => void; refreshKey: number; onChanged: () => void }) {
  const [rows, setRows] = useState<PriceImport[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(() => {
    listImports().then(setRows).catch((e) => onError(String(e)));
  }, [onError]);
  useEffect(load, [load, refreshKey]);

  const latestApplied = rows?.filter((r) => r.status === "applied").sort((a, b) => (b.applied_at ?? "").localeCompare(a.applied_at ?? ""))[0];

  const undo = async (r: PriceImport) => {
    setBusy(r.id);
    try {
      await undoImport(r.id);
      load();
      onChanged();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="overflow-x-auto rounded border border-slate-200">
      <table className="w-full min-w-[720px] text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
          <tr>
            <th className="px-3 py-2">Sheet</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">What it did</th>
            <th className="px-3 py-2">Who, when</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows?.map((r) => {
            const a = r.summary.applied;
            const c = r.summary.counts;
            return (
              <tr key={r.id} className="border-t border-slate-100">
                <td className="px-3 py-2">
                  {r.supplier} · {r.filename}
                </td>
                <td className="px-3 py-2 text-xs">{r.status === "staged" ? "checked, not imported" : r.status}</td>
                <td className="px-3 py-2 text-xs text-slate-600">
                  {a
                    ? `${a.added} added · ${a.updated} repriced · ${a.confirmed} confirmed · ${a.materials} materials · ${a.skipped} skipped`
                    : c
                      ? `${c.new} new · ${c.changed} changed · ${c.problem} problems`
                      : ""}
                </td>
                <td className="px-3 py-2 text-xs text-slate-600">
                  {r.status === "undone"
                    ? `undone by ${r.undone_by}, ${when(r.undone_at)}`
                    : r.applied_by
                      ? `${r.applied_by}, ${when(r.applied_at)}`
                      : `${r.created_by}, ${when(r.created_at)}`}
                </td>
                <td className="px-3 py-2 text-right">
                  {latestApplied?.id === r.id && (
                    <button
                      type="button"
                      disabled={busy === r.id}
                      onClick={() => undo(r)}
                      className="rounded border border-slate-400 px-2 py-0.5 text-xs"
                    >
                      {busy === r.id ? "Undoing…" : "Undo"}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
          {rows && rows.length === 0 && (
            <tr>
              <td colSpan={5} className="px-3 py-6 text-center text-slate-400">
                No price sheets imported yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
