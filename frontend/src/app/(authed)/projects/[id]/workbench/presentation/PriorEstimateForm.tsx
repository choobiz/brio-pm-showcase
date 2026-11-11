"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import type { PriorClientEstimate } from "@/lib/workbench";
import { setPriorClientEstimateAction } from "./actions";

/**
 * Persist the prior estimate the client already saw. When set, every generated
 * presentation includes the "Why this differs from our earlier estimate" section
 * — no longer a one-off manual input.
 */
export function PriorEstimateForm({
  projectId,
  initial,
}: {
  projectId: string;
  initial: PriorClientEstimate | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [total, setTotal] = useState(initial?.total != null ? String(initial.total) : "");
  const [date, setDate] = useState(initial?.date ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  const save = async () => {
    setBusy(true);
    setSaved(false);
    const v: PriorClientEstimate | null = total.trim()
      ? { total: parseFloat(total), date: date || null, note: note || null }
      : null;
    try {
      await setPriorClientEstimateAction(projectId, v);
      setSaved(true);
      router.refresh();
    } finally {
      setBusy(false);
    }
  };

  const field = "mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm";
  const summary = initial?.total != null
    ? `Prior estimate on file: $${Number(initial.total).toLocaleString("en-CA")}${initial.date ? ` (${initial.date})` : ""}`
    : "No prior client estimate set";

  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between text-left">
        <span className="text-sm text-slate-700">{summary}</span>
        <span className="text-xs text-slate-400">{open ? "hide" : "edit"}</span>
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-slate-500">
            The figure/date/context the client already received. When set, each presentation explains why the current total differs.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block text-xs text-slate-500">Prior total (CAD)
              <input value={total} onChange={(e) => setTotal(e.target.value)} inputMode="decimal" placeholder="142572.94" className={field} />
            </label>
            <label className="block text-xs text-slate-500">Date shown to client
              <input value={date} onChange={(e) => setDate(e.target.value)} placeholder="October 10, 2025" className={field} />
            </label>
          </div>
          <label className="block text-xs text-slate-500">Context (what the prior estimate was based on)
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="based on the original architectural scope, before the City required a stormwater management plan" className={field} />
          </label>
          <div className="flex items-center gap-3">
            <button onClick={save} disabled={busy} className="rounded-md px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60" style={{ backgroundColor: "#0068A0" }}>
              {busy ? "Saving…" : "Save"}
            </button>
            {saved && <span className="text-xs text-green-700">Saved ✓ — regenerate the presentation to apply.</span>}
          </div>
        </div>
      )}
    </div>
  );
}
