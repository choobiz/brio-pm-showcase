"use client";

import { useState } from "react";

import { ROLE_LABEL } from "@/lib/milestones";
import { resetChecklist, saveChecklist, type Checklist } from "@/lib/submittals";

type Row = { key?: string; label: string; what: string };

/**
 * Edit a trade checklist, or add one (Amir, 2026-10-17). The change is for every
 * project; a review that already ran keeps the copy it ran with. A built-in can
 * go back to its default; an added one can be removed.
 */
export function ChecklistEditor({
  checklist,
  onClose,
  onSaved,
}: {
  checklist: Checklist | null; // null = a new one
  onClose: () => void;
  onSaved: (key: string | null) => void;
}) {
  const [key, setKey] = useState(checklist?.key ?? "");
  const [label, setLabel] = useState(checklist?.label ?? "");
  const [role, setRole] = useState(checklist?.engineer_role ?? "structural_engineer");
  const [rows, setRows] = useState<Row[]>(checklist?.checks.map((c) => ({ ...c })) ?? [{ label: "", what: "" }]);
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const inp = "w-full rounded border border-slate-300 px-2 py-1 text-[13px]";
  const newKey = (label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "").slice(0, 40);

  const move = (i: number, d: number) =>
    setRows((r) => {
      const n = [...r];
      const j = i + d;
      if (j < 0 || j >= n.length) return r;
      [n[i], n[j]] = [n[j], n[i]];
      return n;
    });

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const k = checklist?.key ?? (key || newKey);
      await saveChecklist(k, {
        label: label.trim(),
        engineer_role: role,
        checks: rows.filter((r) => r.label.trim()).map((r) => ({ key: r.key, label: r.label.trim(), what: r.what.trim() })),
        governing: checklist?.governing ?? [],
        governing_words: words
          .split(",")
          .map((w) => w.trim())
          .filter(Boolean),
      });
      onSaved(k);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!checklist) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await resetChecklist(checklist.key);
      onSaved(r.builtin ? checklist.key : null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true">
      <div className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-md bg-white p-5 text-sm shadow-lg">
        <div className="flex items-baseline justify-between">
          <h2 className="m-0 text-lg font-semibold">{checklist ? "Edit checklist" : "New checklist"}</h2>
          <button type="button" onClick={onClose} className="text-slate-600">
            Close
          </button>
        </div>
        <p className="m-0 mb-3 text-xs text-slate-500">
          This changes the checklist for every project. Reviews that already ran keep the version they ran with.
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="text-[13px]">
            Name
            <input className={inp} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Drywall and board" />
          </label>
          <label className="text-[13px]">
            Who answers design questions
            <select className={inp} value={role} onChange={(e) => setRole(e.target.value)}>
              {Object.entries(ROLE_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          {!checklist && (
            <label className="text-[13px]">
              Short code
              <input className={inp} value={key || newKey} onChange={(e) => setKey(e.target.value.toLowerCase())} />
            </label>
          )}
        </div>

        <div className="mt-3 text-[13px] font-semibold">Checks, in order</div>
        <div className="flex flex-col gap-2">
          {rows.map((r, i) => (
            <div key={i} className="grid grid-cols-[1fr_2fr_auto] items-start gap-2">
              <input
                className={inp}
                placeholder="Check"
                value={r.label}
                onChange={(e) => setRows((x) => x.map((y, j) => (j === i ? { ...y, label: e.target.value } : y)))}
              />
              <textarea
                rows={2}
                className={inp}
                placeholder="What to compare on both sides"
                value={r.what}
                onChange={(e) => setRows((x) => x.map((y, j) => (j === i ? { ...y, what: e.target.value } : y)))}
              />
              <div className="flex gap-1">
                <button type="button" aria-label="Move up" onClick={() => move(i, -1)} className="rounded border border-slate-300 px-1.5">
                  ↑
                </button>
                <button type="button" aria-label="Move down" onClick={() => move(i, 1)} className="rounded border border-slate-300 px-1.5">
                  ↓
                </button>
                <button
                  type="button"
                  aria-label="Remove check"
                  onClick={() => setRows((x) => x.filter((_, j) => j !== i))}
                  className="rounded border border-slate-300 px-1.5 text-red-700"
                >
                  ×
                </button>
              </div>
            </div>
          ))}
          <button type="button" onClick={() => setRows((x) => [...x, { label: "", what: "" }])} className="self-start text-blue-700">
            Add a check
          </button>
        </div>

        <label className="mt-3 block text-[13px]">
          Also suggest governing files whose names contain (comma-separated)
          <input className={inp} value={words} onChange={(e) => setWords(e.target.value)} placeholder="e.g. ISMP, grading" />
        </label>

        <div className="mt-4 flex flex-wrap justify-between gap-2">
          <div>
            {checklist?.builtin && checklist.edited && (
              <button type="button" disabled={busy} onClick={reset} className="rounded-md border border-slate-300 px-3 py-1.5">
                Back to the default
              </button>
            )}
            {checklist && !checklist.builtin && (
              <button type="button" disabled={busy} onClick={reset} className="rounded-md border border-red-300 px-3 py-1.5 text-red-700">
                Remove this checklist
              </button>
            )}
          </div>
          <button
            type="button"
            disabled={busy || !label.trim() || !rows.some((r) => r.label.trim())}
            onClick={save}
            className="rounded-md bg-slate-900 px-4 py-1.5 text-white disabled:opacity-50"
          >
            {busy ? "Saving…" : "Save for every project"}
          </button>
        </div>
        {err && <p className="m-0 mt-2 text-red-700">{err}</p>}
      </div>
    </div>
  );
}
