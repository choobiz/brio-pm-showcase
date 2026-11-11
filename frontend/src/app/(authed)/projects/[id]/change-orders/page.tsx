"use client";

import { use, useEffect, useState } from "react";
import GenerateChangeOrder from "./GenerateChangeOrder";
import EditChangeOrder from "./EditChangeOrder";
import EditWithPrompt from "@/components/EditWithPrompt";

import { Card } from "@/components/ui/card";
import {
  type ChangeOrder,
  listChangeOrders, createChangeOrder, submitChangeOrder, voidChangeOrder, changeOrderPdf,
} from "@/lib/changeOrders";

function money(n: number): string {
  const s = Math.abs(n).toLocaleString("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 });
  return n < 0 ? `−${s}` : s;
}
const STATUS: Record<string, { label: string; cls: string }> = {
  draft: { label: "Draft", cls: "bg-slate-100 text-slate-600" },
  pending: { label: "Awaiting owner", cls: "bg-amber-100 text-amber-800" },
  approved: { label: "Approved", cls: "bg-emerald-100 text-emerald-700" },
  rejected: { label: "Rejected", cls: "bg-red-100 text-red-700" },
  void: { label: "Void", cls: "bg-slate-100 text-slate-400" },
};

export default function ChangeOrdersPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [cos, setCos] = useState<ChangeOrder[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [cost, setCost] = useState("");
  const [days, setDays] = useState("");
  // Which change order is open for editing, by id. One at a time.
  const [editing, setEditing] = useState<string | null>(null);
  // Which change order is currently rendering a PDF, by id.
  const [pdfBusy, setPdfBusy] = useState<string | null>(null);

  async function makePdf(coId: string) {
    setPdfBusy(coId); setErr(null);
    try {
      const res = await changeOrderPdf(id, coId);
      // Opened rather than downloaded: the signed URL expires, and the point
      // of this button is to READ the document before it goes to a client.
      window.open(res.download_url, "_blank", "noopener");
    } catch (e) { setErr(String(e)); } finally { setPdfBusy(null); }
  }

  const load = () => listChangeOrders(id).then(setCos).catch((e) => setErr(String(e)));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [id]);

  async function add() {
    if (!title.trim()) return;
    setBusy(true); setErr(null);
    try {
      await createChangeOrder(id, {
        title: title.trim(), description: desc.trim() || undefined,
        cost_impact: Number(cost) || 0, schedule_impact_days: Number(days) || 0,
      });
      setTitle(""); setDesc(""); setCost(""); setDays("");
      await load();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }
  async function act(fn: Promise<unknown>) {
    setErr(null);
    try { await fn; await load(); } catch (e) { setErr(String(e)); }
  }

  const approvedTotal = cos.filter((c) => c.status === "approved").reduce((s, c) => s + c.cost_impact_incl_gst, 0);

  return (
    <div className="space-y-5 max-w-3xl">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>Change Orders</h1>
        {approvedTotal !== 0 && (
          <div className="text-sm text-slate-500">Approved changes: <b>{money(approvedTotal)}</b> (incl. GST)</div>
        )}
      </div>
      {err && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}

      <GenerateChangeOrder projectId={id} onGenerated={load} />

      {/* New CO */}
      <Card className="p-4 space-y-3">
        <div className="text-sm font-semibold uppercase tracking-wide text-slate-500">New change order</div>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (e.g. Upgrade to cedar cladding)"
               className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
        <textarea value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Description / reason" rows={2}
                  className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
        <div className="flex gap-3">
          <label className="text-xs text-slate-500 flex-1">Cost impact (pre-tax, − for credit)
            <input value={cost} onChange={(e) => setCost(e.target.value)} type="number" placeholder="0"
                   className="mt-1 w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
          </label>
          <label className="text-xs text-slate-500 w-32">Schedule (days ±)
            <input value={days} onChange={(e) => setDays(e.target.value)} type="number" placeholder="0"
                   className="mt-1 w-full border border-slate-300 rounded px-2 py-1.5 text-sm" />
          </label>
        </div>
        <button onClick={add} disabled={busy || !title.trim()}
                className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
          {busy ? "Adding…" : "Add draft"}
        </button>
      </Card>

      {/* List */}
      <div className="space-y-2">
        {cos.map((c) => {
          const s = STATUS[c.status];
          return (
            <Card key={c.id} className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-medium text-slate-800">{c.reference} — {c.title}</div>
                  {c.description && <div className="text-sm text-slate-500 mt-0.5">{c.description}</div>}
                  <div className="mt-1 text-sm text-slate-600">
                    {money(c.cost_impact_incl_gst)} incl. GST
                    {c.schedule_impact_days !== 0 && <span> · {c.schedule_impact_days > 0 ? "+" : ""}{c.schedule_impact_days} days</span>}
                  </div>
                  {c.owner_note && <div className="mt-1 text-xs text-slate-500 italic">Client note: {c.owner_note}</div>}
                </div>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${s.cls}`}>{s.label}</span>
              </div>

              {/* The working behind the number. A change order asks a client to
                  pay more than they agreed, so the total alone is not enough:
                  this is what gets walked through line by line, and what should
                  be checked before sending. Collapsed, since most of the time
                  the total is all anyone wants. */}
              {c.detail?.line_items?.length ? (
                <details className="mt-3 rounded border border-slate-200 bg-slate-50 px-3 py-2">
                  <summary className="cursor-pointer text-xs font-medium text-slate-600">
                    Pricing breakdown, {c.detail.line_items.length} item{c.detail.line_items.length === 1 ? "" : "s"}
                    {c.detail.generated ? " (generated)" : ""}
                  </summary>
                  <div className="overflow-x-auto">
                    <table className="mt-2 w-full min-w-[34rem] text-xs">
                      <thead className="text-slate-500">
                        <tr>
                          <th className="py-1 text-left font-medium">Item</th>
                          <th className="w-16 py-1 text-right font-medium">Qty</th>
                          <th className="w-14 py-1 text-left font-medium">Unit</th>
                          <th className="w-24 py-1 text-right font-medium">Unit $</th>
                          <th className="w-24 py-1 text-right font-medium">Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {c.detail.line_items.map((li, i) => (
                          <tr key={i} className="border-t border-slate-200 align-top">
                            <td className="py-1 pr-2 text-slate-700">{li.description}</td>
                            <td className="py-1 text-right tabular-nums">{li.qty ?? ""}</td>
                            <td className="py-1 text-slate-500">{li.unit ?? ""}</td>
                            <td className="py-1 text-right tabular-nums">{li.unit_cost != null ? money(li.unit_cost) : ""}</td>
                            <td className="py-1 text-right tabular-nums">{li.amount != null ? money(li.amount) : ""}</td>
                          </tr>
                        ))}
                        <tr className="border-t-2 border-slate-300 font-medium">
                          <td className="py-1" colSpan={4}>Total, pre-tax</td>
                          <td className="py-1 text-right tabular-nums">{money(c.cost_impact)}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  {c.detail.assumptions?.length ? (
                    <div className="mt-3">
                      <div className="text-xs font-medium text-slate-600">Assumptions</div>
                      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-slate-600">
                        {c.detail.assumptions.map((a, i) => <li key={i}>{a}</li>)}
                      </ul>
                    </div>
                  ) : null}
                  {c.detail.exclusions?.length ? (
                    <div className="mt-3">
                      <div className="text-xs font-medium text-slate-600">Exclusions</div>
                      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-slate-600">
                        {c.detail.exclusions.map((x, i) => <li key={i}>{x}</li>)}
                      </ul>
                    </div>
                  ) : null}
                </details>
              ) : null}

              {editing === c.id && (
                <EditChangeOrder
                  projectId={id}
                  co={c}
                  onSaved={() => { setEditing(null); load(); }}
                  onCancel={() => setEditing(null)}
                />
              )}

              {(c.status === "draft" || c.status === "pending") && editing !== c.id && (
                <EditWithPrompt
                  projectId={id}
                  recordType="change_order"
                  recordId={c.id}
                  label={c.reference}
                  onApplied={load}
                />
              )}

              {c.status !== "void" && editing !== c.id && (
                <div className="mt-2 flex items-center gap-3">
                  <button onClick={() => makePdf(c.id)} disabled={pdfBusy === c.id}
                          className="text-xs font-medium text-blue-700 hover:underline disabled:text-slate-400">
                    {pdfBusy === c.id ? "Generating…" : "Generate PDF"}
                  </button>
                  {(c.status === "draft" || c.status === "pending") && (
                    <>
                      <button onClick={() => setEditing(c.id)}
                              className="text-xs font-medium text-slate-600 hover:underline">Edit</button>
                      {c.status === "draft" && (
                        <button onClick={() => act(submitChangeOrder(id, c.id))}
                                className="text-xs font-medium text-blue-600 hover:underline">Send to client →</button>
                      )}
                      <button onClick={() => act(voidChangeOrder(id, c.id))}
                              className="text-xs text-slate-400 hover:text-red-600">Void</button>
                    </>
                  )}
                </div>
              )}
            </Card>
          );
        })}
        {cos.length === 0 && <p className="text-sm text-slate-400">No change orders yet.</p>}
      </div>
    </div>
  );
}
