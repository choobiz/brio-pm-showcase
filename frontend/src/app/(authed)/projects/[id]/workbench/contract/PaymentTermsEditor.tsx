"use client";

import { useEffect, useState } from "react";

import {
  type TermEntry, type Template,
  listTemplates, saveTemplate, deleteTemplate, getProjectTerms, setProjectTerms,
} from "@/lib/paymentTerms";

export function PaymentTermsEditor({ projectId }: { projectId: string }) {
  const [entries, setEntries] = useState<TermEntry[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [hasContract, setHasContract] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saveName, setSaveName] = useState("");
  const [showSave, setShowSave] = useState(false);
  const [pickedTpl, setPickedTpl] = useState("");

  const loadTemplates = () => listTemplates().then(setTemplates).catch(() => {});
  useEffect(() => {
    getProjectTerms(projectId).then((r) => { setHasContract(r.has_contract); setEntries(r.entries || []); }).catch((e) => setErr(String(e)));
    loadTemplates();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const sum = Math.round(entries.reduce((a, e) => a + (Number(e.pct) || 0), 0) * 100) / 100;
  const sumOk = sum === 100;

  const upd = (i: number, k: keyof TermEntry, v: string) =>
    setEntries((es) => es.map((e, j) => (j === i ? { ...e, [k]: k === "pct" ? (v === "" ? 0 : Number(v)) : v } : e)));
  const addRow = () => setEntries((es) => [...es, { milestone: "", pct: 0, detail: "" }]);
  const rmRow = (i: number) => setEntries((es) => es.filter((_, j) => j !== i));

  function loadTpl(id: string) {
    setPickedTpl(id);
    const t = templates.find((x) => x.id === id);
    if (t) { setEntries(t.entries.map((e) => ({ ...e }))); setMsg(null); setErr(null); }
  }

  async function apply() {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await setProjectTerms(projectId, entries);
      setMsg(`Applied to contract — the schedule (${r.entries.length} draws, ${r.sum}%) now shows on the contract PDF.`);
    } catch (e) { setErr(String(e).replace("Error: ", "")); } finally { setBusy(false); }
  }
  async function saveAsTemplate() {
    if (!saveName.trim()) return;
    setBusy(true); setErr(null);
    try {
      await saveTemplate({ name: saveName.trim(), entries });
      setSaveName(""); setShowSave(false); setMsg("Saved as a custom template.");
      loadTemplates();
    } catch (e) { setErr(String(e).replace("Error: ", "")); } finally { setBusy(false); }
  }
  async function delTpl() {
    const t = templates.find((x) => x.id === pickedTpl);
    if (!t || t.is_builtin) return;
    if (typeof window !== "undefined" && !window.confirm(`Delete custom template "${t.name}"?`)) return;
    setBusy(true);
    try { await deleteTemplate(t.id); setPickedTpl(""); loadTemplates(); } finally { setBusy(false); }
  }

  const inp = "rounded border border-slate-300 px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500";
  const pickedCustom = templates.find((x) => x.id === pickedTpl && !x.is_builtin);

  return (
    <div className="mt-8 rounded-lg border border-slate-200 p-4">
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-3">
        <h2 className="text-base font-semibold">Payment Terms</h2>
        <div className="flex items-center gap-2">
          <select value={pickedTpl} onChange={(e) => loadTpl(e.target.value)} className={inp}>
            <option value="">Load a template…</option>
            <optgroup label="Built-in">
              {templates.filter((t) => t.is_builtin).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </optgroup>
            {templates.some((t) => !t.is_builtin) && (
              <optgroup label="My templates">
                {templates.filter((t) => !t.is_builtin).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </optgroup>
            )}
          </select>
          {pickedCustom && <button onClick={delTpl} className="text-xs text-slate-400 hover:text-red-600">delete template</button>}
        </div>
      </div>

      {!hasContract && (
        <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          No contract yet — generate the contract above first, then you can apply a payment schedule.
        </div>
      )}
      {err && <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}
      {msg && <div className="mb-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{msg}</div>}

      <table className="w-full text-sm">
        <thead>
          <tr className="text-[11px] uppercase tracking-wide text-slate-400 text-left">
            <th className="py-1 pr-2">Milestone</th>
            <th className="py-1 px-2 w-16">%</th>
            <th className="py-1 px-2">Description</th>
            <th className="w-8"></th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => (
            <tr key={i} className="align-top border-t border-slate-100">
              <td className="py-1.5 pr-2"><input value={e.milestone} onChange={(ev) => upd(i, "milestone", ev.target.value)} placeholder="Milestone" className={`${inp} w-full`} /></td>
              <td className="py-1.5 px-2"><input type="number" min={0} max={100} value={e.pct} onChange={(ev) => upd(i, "pct", ev.target.value)} className={`${inp} w-16 text-right`} /></td>
              <td className="py-1.5 px-2"><textarea value={e.detail || ""} onChange={(ev) => upd(i, "detail", ev.target.value)} rows={1} placeholder="What this draw covers…" className={`${inp} w-full resize-y`} /></td>
              <td className="py-1.5 text-right"><button onClick={() => rmRow(i)} className="text-slate-300 hover:text-red-600" title="Remove">✕</button></td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="flex items-center justify-between mt-2">
        <button onClick={addRow} className="text-sm text-blue-700 hover:underline">+ Add draw</button>
        <div className={`text-sm font-semibold ${sumOk ? "text-emerald-700" : "text-red-600"}`}>
          Total: {sum}% {sumOk ? "✓" : "(must equal 100%)"}
        </div>
      </div>

      <div className="flex items-center gap-2 mt-4 flex-wrap">
        <button onClick={apply} disabled={busy || !sumOk || !hasContract || entries.length === 0}
          className="rounded-md bg-[var(--color-brio-primary)] px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50">
          {busy ? "Working…" : "Apply to contract"}
        </button>
        {showSave ? (
          <span className="flex items-center gap-1.5">
            <input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder="Template name" className={inp} />
            <button onClick={saveAsTemplate} disabled={busy || !saveName.trim()} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50">Save</button>
            <button onClick={() => setShowSave(false)} className="text-sm text-slate-500">cancel</button>
          </span>
        ) : (
          <button onClick={() => setShowSave(true)} disabled={entries.length === 0} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50">Save as template</button>
        )}
      </div>
      <p className="mt-2 text-xs text-slate-400">Applying writes the schedule into this project's contract, and it renders on the contract, cash-flow, and package PDFs. The final draw is typically a 10% completion holdback (BC Builders Lien Act).</p>
    </div>
  );
}
