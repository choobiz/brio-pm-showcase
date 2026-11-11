"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import {
  type PlannedSelection,
  type SelectionInput,
  type SelectionPlan,
  type SelectionSuggestion,
  confirmSelection,
  createSelection,
  deleteSelection,
  getSelectionPlan,
  getSelectionSuggestions,
  sendSelection,
  updateSelection,
} from "@/lib/selections";
import { errorMessage } from "@/lib/workspaceApi";

/**
 * Client › Selections & approvals (round 2 board R2Selections, 2026-11-02).
 * The decisions the client owes us, in the order the work needs them:
 * decide-by = the activity's start (latest schedule) minus the supplier's
 * lead time. A selection is a draft until "Send to client". Confirming a
 * choice that costs more or less than quoted drafts a change order.
 */

type OptDraft = { label: string; description: string; price_delta: string };
type Form = {
  id: string | null; title: string; category: string; description: string;
  decision_type: "select" | "approve"; purchaser: "" | "brio" | "client";
  activity_code: string; lead_time_days: string; allowance: string; opts: OptDraft[];
};
const EMPTY: Form = {
  id: null, title: "", category: "", description: "", decision_type: "select", purchaser: "",
  activity_code: "", lead_time_days: "", allowance: "", opts: [{ label: "", description: "", price_delta: "" }],
};

const money = (n: number | null | undefined) =>
  n == null ? "—" : `${n < 0 ? "−" : ""}${Math.abs(n).toLocaleString("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 })}`;
function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
}

export default function SelectionsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [plan, setPlan] = useState<SelectionPlan | null>(null);
  const [sugs, setSugs] = useState<SelectionSuggestion[] | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => getSelectionPlan(id).then(setPlan, (e) => setErr(errorMessage(e))), [id]);
  useEffect(() => { load(); }, [load]);

  async function run(fn: () => Promise<unknown>, done?: string) {
    setBusy(true); setErr(null); setNote(null);
    try { await fn(); await load(); if (done) setNote(done); } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  }

  function edit(s: PlannedSelection) {
    setForm({
      id: s.id, title: s.title, category: s.category ?? "", description: s.description ?? "",
      decision_type: s.decision_type, purchaser: s.purchaser ?? "", activity_code: s.activity_code ?? "",
      lead_time_days: s.lead_time_days?.toString() ?? "", allowance: s.allowance?.toString() ?? "",
      opts: s.options.length
        ? s.options.map((o) => ({ label: o.label, description: o.description ?? "", price_delta: o.price_delta?.toString() ?? "" }))
        : EMPTY.opts,
    });
  }

  async function save() {
    if (!form || !form.title.trim()) return;
    const body: SelectionInput = {
      title: form.title.trim(), category: form.category.trim() || null, description: form.description.trim() || null,
      decision_type: form.decision_type, purchaser: form.purchaser || null,
      activity_code: form.activity_code || null,
      lead_time_days: form.lead_time_days === "" ? null : Number(form.lead_time_days),
      allowance: form.allowance === "" ? null : Number(form.allowance),
      options: form.decision_type === "select"
        ? form.opts.filter((o) => o.label.trim()).map((o, i) => ({
            id: `opt-${i + 1}`, label: o.label.trim(), description: o.description.trim() || null,
            price_delta: o.price_delta.trim() === "" ? null : Number(o.price_delta),
          }))
        : [],
    };
    await run(async () => {
      if (form.id) await updateSelection(id, form.id, body); else await createSelection(id, body);
      setForm(null);
    }, form.id ? "Saved." : "Added as a draft. The client doesn't see it until you send it.");
  }

  async function addSuggestion(s: SelectionSuggestion) {
    await run(async () => {
      await createSelection(id, { title: s.title, activity_code: s.activity_code, lead_time_days: s.lead_time_days, options: [] });
      setSugs((cur) => (cur ?? []).filter((x) => x.title !== s.title));
    }, `Added "${s.title}" as a draft. Add its options, then send it.`);
  }

  const status = (s: PlannedSelection) => {
    if (s.state === "confirmed") return { text: s.change_order_id ? "Confirmed · change order drafted" : "Confirmed", cls: "border-green-700 text-green-800" };
    if (s.state === "chose") return { text: "Client chose · you confirm", cls: "border-blue-700 text-blue-800" };
    if (s.state === "waiting") return { text: `Waiting · sent ${s.days_waiting ?? 0} day${s.days_waiting === 1 ? "" : "s"} ago`, cls: "border-orange-700 text-orange-800" };
    return { text: "Not sent yet", cls: "border-slate-300 text-slate-600" };
  };
  const inp = "rounded border border-slate-300 px-2 py-1.5 text-sm";
  const btn = "rounded border border-slate-900 px-2.5 py-1 text-xs font-medium disabled:opacity-50";
  const pri = "rounded bg-[var(--color-brio-primary)] px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50";

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Selections &amp; approvals</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">
            Decisions the client owes us, in the order the work needs them. Decide by = when the work starts (latest schedule), minus the supplier&apos;s lead time.
            A selection is a draft until you send it; the client is emailed then.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={btn} disabled={busy}
            onClick={() => getSelectionSuggestions(id).then(setSugs, (e) => setErr(errorMessage(e)))}>
            Suggest from the schedule
          </button>
          <button type="button" className={pri} onClick={() => setForm({ ...EMPTY })}>+ New selection</button>
        </div>
      </header>
      {err && <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{err}</div>}
      {note && <div className="rounded border border-green-700 bg-white px-3 py-2 text-sm text-green-800" role="status">{note}</div>}

      {sugs && (
        <section className="rounded border border-slate-200 bg-white px-4 py-3">
          <div className="flex items-baseline justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">Suggested from this project&apos;s schedule</h2>
            <button type="button" className="text-xs underline" onClick={() => setSugs(null)}>Close</button>
          </div>
          {sugs.length === 0 ? <p className="mt-2 text-sm text-slate-600">Nothing to add: every typical decision with work on this schedule is already listed.</p> : (
            <ul className="mt-2 divide-y divide-slate-100 text-sm">
              {sugs.map((s) => (
                <li key={s.title} className="flex flex-wrap items-center gap-2 py-2">
                  <b>{s.title}</b>
                  <span className="text-slate-600">for {s.activity_code} {s.activity_name} · {day(s.start)} · typical lead time {s.lead_time_days} days</span>
                  <button type="button" className={`${btn} ml-auto`} disabled={busy} onClick={() => addSuggestion(s)}>Add as draft</button>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-1 text-xs text-slate-500">Lead times are typical figures; set each one from the supplier&apos;s quote.</p>
        </section>
      )}

      <section className="overflow-x-auto rounded border border-slate-200 bg-white">
        <table className="w-full min-w-[820px] text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
            <tr>
              <th className="px-3 py-2 text-left">Decision</th><th className="px-3 py-2 text-left">Needed for</th>
              <th className="px-3 py-2 text-left">Decide by</th><th className="px-3 py-2 text-right">Allowance</th>
              <th className="px-3 py-2 text-right">Their choice</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {!plan && <tr><td colSpan={7} className="px-3 py-4 text-slate-500">Loading…</td></tr>}
            {plan && plan.selections.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-4 text-slate-600">No selections yet. Use &ldquo;Suggest from the schedule&rdquo; or add one.</td></tr>
            )}
            {plan?.selections.map((s) => {
              const st = status(s);
              return (
                <FragmentRow key={s.id} open={open === s.id}>
                  <tr className="border-t border-slate-100 align-top">
                    <td className="px-3 py-2">
                      <button type="button" className="text-left font-medium" onClick={() => setOpen(open === s.id ? null : s.id)}>
                        {s.title}
                      </button>
                      {s.decision_type === "approve" && <span className="ml-1 text-xs text-slate-500">approval</span>}
                    </td>
                    <td className="px-3 py-2 text-xs">{s.activity ? <>{s.activity.code} · {day(s.activity.start)}</> : <span className="text-slate-500">not linked</span>}</td>
                    <td className={`px-3 py-2 whitespace-nowrap ${s.late ? "font-semibold text-red-700" : ""}`}>
                      {day(s.decide_by)}{s.late && " (late)"}
                      {s.lead_time_days != null && <div className="text-xs font-normal text-slate-500">{s.lead_time_days}-day lead</div>}
                    </td>
                    <td className="px-3 py-2 text-right">{money(s.allowance)}</td>
                    <td className="px-3 py-2 text-right">
                      {s.selected_option ? <>{s.selected_option.label}{s.choice_delta ? <div className="text-xs">{s.choice_delta > 0 ? "+" : ""}{money(s.choice_delta)}</div> : null}</> : "—"}
                    </td>
                    <td className="px-3 py-2"><span className={`rounded border px-1.5 py-0.5 text-xs ${st.cls}`}>{st.text}</span></td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      {s.state === "not_sent" && (
                        <button type="button" className={pri} disabled={busy}
                          onClick={() => run(() => sendSelection(id, s.id), `"${s.title}" sent to the client's portal.`)}>Send to client</button>
                      )}
                      {s.state === "chose" && (
                        <button type="button" className={pri} disabled={busy}
                          onClick={() => run(async () => {
                            const r = await confirmSelection(id, s.id);
                            if (r.change_order) setNote(`Confirmed. Draft CO-${r.change_order.number} made for ${money(r.change_order.cost_impact)}; check it in Money › Change orders before sending.`);
                          }, "Confirmed.")}>
                          Confirm{s.choice_delta ? ` · drafts a CO ${s.choice_delta > 0 ? "+" : ""}${money(s.choice_delta)}` : ""}
                        </button>
                      )}
                    </td>
                  </tr>
                  {open === s.id && (
                    <tr className="bg-slate-50">
                      <td colSpan={7} className="px-3 py-3">
                        {s.description && <p className="mb-2 text-slate-700">{s.description}</p>}
                        {s.decision_type === "select" && (
                          <div className="flex flex-wrap gap-2">
                            {s.options.length === 0 && <span className="text-slate-600">No options yet. Edit to add them before sending.</span>}
                            {s.options.map((o) => (
                              <div key={o.id} className={`min-w-48 flex-1 rounded border bg-white p-2 ${o.id === s.selected_option_id ? "border-2 border-slate-900" : "border-slate-200"}`}>
                                <b>{o.label}</b>{o.description && <div className="text-xs text-slate-600">{o.description}</div>}
                                <div className="text-xs">{o.price_delta ? `${o.price_delta > 0 ? "+" : ""}${money(o.price_delta)}` : "Included"}</div>
                                {o.id === s.selected_option_id && <div className="text-xs font-semibold">Client&apos;s choice</div>}
                              </div>
                            ))}
                          </div>
                        )}
                        {s.client_comment && <p className="mt-2 text-sm">Client&apos;s note: &ldquo;{s.client_comment}&rdquo;</p>}
                        {s.change_order_id && (
                          <p className="mt-2 text-sm">A draft change order was made for the difference: <Link className="text-blue-700" href={`/projects/${id}/money?view=change_orders`}>Money › Change orders</Link>.</p>
                        )}
                        <div className="mt-2 flex flex-wrap gap-3 text-xs">
                          {(s.state === "not_sent" || s.state === "waiting") && <button type="button" className="underline" onClick={() => edit(s)}>Edit</button>}
                          {s.state === "confirmed" && (
                            <button type="button" className="underline" disabled={busy}
                              onClick={() => run(() => updateSelection(id, s.id, { title: s.title, status: "pending" }), "Reopened. Any change order it drafted stays in Money.")}>Reopen</button>
                          )}
                          <DeleteButton title={s.title} onDelete={() => run(() => deleteSelection(id, s.id), "Deleted.")} />
                        </div>
                      </td>
                    </tr>
                  )}
                </FragmentRow>
              );
            })}
          </tbody>
        </table>
      </section>
      <p className="text-xs text-slate-500">
        The client sees sent selections in their portal (Selections), with the decide-by date, photos and the price difference. They pick; you confirm.
        A choice over the allowance becomes a draft change order in Money, under it a credit. Nothing is ordered until you confirm.
      </p>

      {form && (
        <section className="space-y-3 rounded border border-slate-300 bg-white px-4 py-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">{form.id ? "Edit selection" : "New selection"}</h2>
          <div className="grid gap-2 sm:grid-cols-2">
            <input className={inp} aria-label="Title" placeholder="Title (e.g. Garage door)" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            <input className={inp} aria-label="Category" placeholder="Category (optional)" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} />
          </div>
          <textarea className={`${inp} w-full`} rows={2} aria-label="Guidance" placeholder="Guidance for the client (optional)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-1.5">Needed for
              <select className={inp} value={form.activity_code} onChange={(e) => setForm({ ...form, activity_code: e.target.value })}>
                <option value="">— pick the work —</option>
                {plan?.activities.map((a) => <option key={a.code} value={a.code}>{a.code} {a.name} · {day(a.start)}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-1.5">Lead time
              <input className={`${inp} w-20`} type="number" min={0} value={form.lead_time_days} onChange={(e) => setForm({ ...form, lead_time_days: e.target.value })} /> days
            </label>
            <label className="flex items-center gap-1.5">Allowance $
              <input className={`${inp} w-28`} type="number" min={0} step="0.01" value={form.allowance} onChange={(e) => setForm({ ...form, allowance: e.target.value })} />
            </label>
            <label className="flex items-center gap-1.5">Type
              <select className={inp} value={form.decision_type} onChange={(e) => setForm({ ...form, decision_type: e.target.value as "select" | "approve" })}>
                <option value="select">Choose an option</option><option value="approve">Approve / request changes</option>
              </select>
            </label>
            <label className="flex items-center gap-1.5">Bought by
              <select className={inp} value={form.purchaser} onChange={(e) => setForm({ ...form, purchaser: e.target.value as Form["purchaser"] })}>
                <option value="">—</option><option value="brio">BRIO</option><option value="client">Client</option>
              </select>
            </label>
          </div>
          {form.decision_type === "select" && (
            <div className="space-y-2">
              <div className="text-xs text-slate-600">Options. Price is the difference from the allowance: 0 or blank = included, + costs more, − is a credit.</div>
              {form.opts.map((o, i) => (
                <div key={i} className="flex flex-wrap items-center gap-2">
                  <input className={`${inp} min-w-0 flex-1`} aria-label={`Option ${i + 1}`} placeholder="Option (e.g. Upgrade with windows row)" value={o.label}
                    onChange={(e) => setForm({ ...form, opts: form.opts.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })} />
                  <input className={`${inp} w-24`} aria-label={`Price difference ${i + 1}`} placeholder="+/− $" inputMode="decimal" value={o.price_delta}
                    onChange={(e) => setForm({ ...form, opts: form.opts.map((x, j) => (j === i ? { ...x, price_delta: e.target.value } : x)) })} />
                  <button type="button" className="px-1 text-slate-500" aria-label={`Remove option ${i + 1}`}
                    onClick={() => setForm({ ...form, opts: form.opts.filter((_, j) => j !== i) })}>✕</button>
                </div>
              ))}
              <button type="button" className={btn} onClick={() => setForm({ ...form, opts: [...form.opts, { label: "", description: "", price_delta: "" }] })}>+ Add option</button>
            </div>
          )}
          <div className="flex gap-2">
            <button type="button" className={pri} disabled={busy || !form.title.trim()} onClick={save}>{form.id ? "Save" : "Add as draft"}</button>
            <button type="button" className={btn} onClick={() => setForm(null)}>Cancel</button>
          </div>
        </section>
      )}
    </div>
  );
}

function FragmentRow({ children }: { children: React.ReactNode; open: boolean }) {
  return <>{children}</>;
}

function DeleteButton({ title, onDelete }: { title: string; onDelete: () => void }) {
  const [ask, setAsk] = useState(false);
  return ask ? (
    <span>Delete &ldquo;{title}&rdquo;? <button type="button" className="font-semibold text-red-700 underline" onClick={onDelete}>Delete</button>{" "}
      <button type="button" className="underline" onClick={() => setAsk(false)}>Keep</button></span>
  ) : <button type="button" className="text-slate-500 underline" onClick={() => setAsk(true)}>Delete</button>;
}
