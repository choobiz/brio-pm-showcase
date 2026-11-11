"use client";

import { useState } from "react";

import { scopeLabel, type ScopeForMenu, type ScopePackEntry } from "@/lib/scopePackLinks";

/**
 * The "scope + selections + lump sum" entry in the PDF menu. Opens directly
 * with the estimate's own scope; only an estimate with no scope, on a project
 * with several, asks which one to include (Amir, 2026-10-27).
 */
export function ScopePackMenuItem({ entry, scopes }: { entry: ScopePackEntry; scopes: ScopeForMenu[] }) {
  const [asking, setAsking] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const row = "block w-full rounded px-2 py-1.5 text-left text-sm";

  if (entry.kind === "none") {
    return <span className={`${row} cursor-not-allowed text-slate-400`}>{entry.label}</span>;
  }
  if (entry.kind === "direct") {
    return (
      <a href={entry.href} target="_blank" rel="noopener noreferrer" className={`${row} hover:bg-slate-100`}>
        {entry.label}
      </a>
    );
  }
  const sorted = [...scopes].sort((a, b) => b.version - a.version);
  return (
    <>
      <button type="button" onClick={() => setAsking(true)} className={`${row} hover:bg-slate-100`}>
        {entry.label}
      </button>
      {asking && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-md rounded-md border border-slate-300 bg-white p-4 shadow-lg">
            <div className="text-sm font-semibold">Which scope goes in this PDF?</div>
            <p className="mt-1 text-[13px] text-slate-600">
              This estimate isn&apos;t linked to a scope, and the project has {sorted.length}.
            </p>
            <div className="mt-3 space-y-2">
              {sorted.map((s) => (
                <label key={s.id} className="flex cursor-pointer items-start gap-2 rounded border border-slate-200 p-2 text-[13px] hover:bg-slate-50">
                  <input type="radio" name="scope-pack" className="mt-0.5" checked={picked === s.id} onChange={() => setPicked(s.id)} />
                  <span>
                    <span className="font-medium">{scopeLabel(s)}</span>
                    {s.created_at && <span className="text-slate-500"> · {new Date(s.created_at).toLocaleDateString()}</span>}
                    {typeof s.payload?.summary === "string" && s.payload.summary && (
                      <span className="block text-slate-600">{s.payload.summary.slice(0, 120)}</span>
                    )}
                  </span>
                </label>
              ))}
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setAsking(false)} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
                Cancel
              </button>
              <a
                href={picked ? `${entry.base}&scope_id=${picked}` : undefined}
                target="_blank"
                rel="noopener noreferrer"
                aria-disabled={!picked}
                onClick={(e) => (picked ? setAsking(false) : e.preventDefault())}
                className={`rounded px-3 py-1.5 text-sm ${picked ? "bg-slate-900 text-white" : "cursor-not-allowed bg-slate-200 text-slate-400"}`}
              >
                Open PDF
              </a>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
