"use client";

import { useCallback, useEffect, useState } from "react";

import { Box, Label } from "@/components/kit";
import { WorkspaceError } from "@/components/WorkspaceError";
import {
  addBill,
  budgetEstimates,
  deleteBill,
  editBill,
  getCosts,
  previewBudget,
  saveBudget,
  type Bill,
  type BillInput,
  type BudgetEstimate,
  type BudgetPreview,
  type CostKind,
  type CostSummary,
} from "@/lib/costs";
import { getDownloadUrl, uploadDocument } from "@/lib/documents";
import { fmtMoney } from "@/lib/fmt";
import { errorMessage } from "@/lib/workspaceApi";

/**
 * Money › Costs (cost entry, 2026-10-30). Bills BRIO receives, entered by the
 * office, against a budget per division. The budget is the contract spread by
 * one estimate's proportions plus each approved change order, so it is a share
 * of the PRICE (overhead and profit included); the page says so.
 */

const KIND_LABEL: Record<CostKind, string> = {
  supplier_invoice: "Supplier",
  subtrade_invoice: "Subtrade",
  other: "Other",
};

const cents = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("en-CA", { style: "currency", currency: "CAD" });

const today = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Vancouver" });

export default function Costs({ projectId }: { projectId: string }) {
  const [c, setC] = useState<CostSummary | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [editing, setEditing] = useState<Bill | "new" | null>(null);
  const [budgetOpen, setBudgetOpen] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    getCosts(projectId).then((s) => { setC(s); setErr(null); }).catch(setErr);
  }, [projectId]);
  useEffect(load, [load]);

  if (!c) return err != null ? <WorkspaceError error={err} onRetry={load} /> : <p className="text-sm text-slate-500">Loading…</p>;

  const remove = async (b: Bill) => {
    if (!confirm(`Delete the ${b.vendor} bill of ${cents(b.amount)}${b.invoice_number ? ` (${b.invoice_number})` : ""}?`)) return;
    try {
      await deleteBill(projectId, b.id);
      setNote(`Deleted the ${b.vendor} bill.`);
      load();
    } catch (e) {
      setErr(e);
    }
  };

  const openFile = async (docId: string) => {
    try {
      const r = await getDownloadUrl(docId);
      window.open(r.download_url, "_blank", "noopener");
    } catch (e) {
      setErr(e);
    }
  };

  const revised = c.contract?.revised ?? null;
  const kpis: [string, string, string?][] = [
    ["Spent", fmtMoney(c.spent_before_gst), revised ? `before GST, of ${fmtMoney(revised)} contract + approved COs` : "before GST"],
    ["Budget", fmtMoney(c.budget_total), c.budget_total == null ? "not set yet" : "by division, below"],
    ["Left", c.budget_total == null ? "—" : fmtMoney(c.budget_total - c.spent_before_gst)],
    ["GST paid", fmtMoney(c.gst), `${c.bills.length} bill${c.bills.length === 1 ? "" : "s"}`],
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {kpis.map(([label, v, sub]) => (
          <Box key={label} className="p-4">
            <Label>{label}</Label>
            <div className="mt-1 text-xl font-bold tabular-nums">{v}</div>
            {sub && <div className="text-xs text-slate-500">{sub}</div>}
          </Box>
        ))}
      </div>

      {err != null && <WorkspaceError error={err} />}
      {note && <p className="m-0 text-[13px] text-emerald-800">{note}</p>}

      {editing && (
        <BillForm
          projectId={projectId}
          summary={c}
          bill={editing === "new" ? null : editing}
          onDone={(msg) => { setEditing(null); if (msg) setNote(msg); load(); }}
        />
      )}

      <Box className="overflow-x-auto p-0">
        <div className="flex flex-wrap items-center justify-between gap-2 px-5 pb-2 pt-4">
          <Label>By division</Label>
          <button type="button" onClick={() => setBudgetOpen((o) => !o)} className="rounded-md border border-slate-900 px-3 py-1.5 text-sm">
            {c.budget_total == null ? "Set the budget" : "Reset the budget"}
          </button>
        </div>
        {c.budget_total != null && (
          <p className="m-0 px-5 pb-2 text-xs text-slate-600">
            The budget is each division&apos;s share of the contract price, so it includes your overhead and profit.
            A division under budget can still be eating margin.
          </p>
        )}
        <table className="w-full min-w-[560px] text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
            <tr className="border-b border-slate-300">
              <th className="px-5 py-2">Division</th>
              <th className="px-3 py-2 text-right">Budget</th>
              <th className="px-3 py-2 text-right">Spent</th>
              <th className="px-3 py-2 text-right">Left</th>
            </tr>
          </thead>
          <tbody>
            {c.by_division.map((d) => (
              <tr key={d.division} className="border-b border-slate-100">
                <td className="px-5 py-2">{d.division}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtMoney(d.budget)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtMoney(d.spent)}</td>
                <td className={`px-3 py-2 text-right tabular-nums ${d.over ? "font-semibold text-red-700" : ""}`}>
                  {d.left == null ? "—" : d.over ? `${fmtMoney(-d.left)} over` : fmtMoney(d.left)}
                </td>
              </tr>
            ))}
            {c.unassigned_spent !== 0 && (
              <tr className="border-b border-slate-100">
                <td className="px-5 py-2 text-orange-800">No division</td>
                <td className="px-3 py-2 text-right">—</td>
                <td className="px-3 py-2 text-right tabular-nums">{fmtMoney(c.unassigned_spent)}</td>
                <td className="px-3 py-2 text-right">—</td>
              </tr>
            )}
            {c.by_division.length === 0 && c.unassigned_spent === 0 && (
              <tr><td colSpan={4} className="px-5 py-4 text-slate-500">No budget and no bills yet.</td></tr>
            )}
          </tbody>
        </table>
      </Box>

      {budgetOpen && (
        <BudgetPanel
          projectId={projectId}
          replacing={c.budget_total != null}
          onDone={(s) => { setBudgetOpen(false); if (s) { setC(s); setNote("Budget saved."); } }}
        />
      )}

      <Box className="overflow-x-auto p-0">
        <div className="flex flex-wrap items-center justify-between gap-2 px-5 pb-2 pt-4">
          <Label>Bills</Label>
          <button type="button" onClick={() => { setNote(null); setEditing("new"); }} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white">
            + Add a bill
          </button>
        </div>
        <table className="w-full min-w-[720px] text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
            <tr className="border-b border-slate-300">
              <th className="px-5 py-2">Date</th>
              <th className="px-3 py-2">From</th>
              <th className="px-3 py-2">Division</th>
              <th className="px-3 py-2 text-right">Before GST</th>
              <th className="px-3 py-2 text-right">GST</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {c.bills.map((b) => (
              <tr key={b.id} className="border-b border-slate-100 align-top">
                <td className="whitespace-nowrap px-5 py-2">{b.cost_date}</td>
                <td className="px-3 py-2">
                  {b.vendor}
                  <span className="ml-1 text-xs text-slate-500">
                    {KIND_LABEL[b.kind] ?? b.kind}{b.invoice_number ? ` · ${b.invoice_number}` : ""}
                  </span>
                  {b.description && <div className="text-xs text-slate-600">{b.description}</div>}
                </td>
                <td className="px-3 py-2 text-[13px]">{b.division ?? <span className="text-orange-800">none</span>}</td>
                <td className="px-3 py-2 text-right tabular-nums">{cents(b.amount)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{cents(b.gst)}</td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-3 text-[13px]">
                    {b.source_document_id && (
                      <button type="button" onClick={() => openFile(b.source_document_id!)} className="text-blue-700">File</button>
                    )}
                    <button type="button" onClick={() => { setNote(null); setEditing(b); }} className="text-blue-700">Edit</button>
                    <button type="button" onClick={() => remove(b)} className="text-red-700">Delete</button>
                  </div>
                </td>
              </tr>
            ))}
            {c.bills.length === 0 && (
              <tr><td colSpan={6} className="px-5 py-4 text-slate-500">No bills entered yet.</td></tr>
            )}
          </tbody>
        </table>
      </Box>
    </div>
  );
}

function BillForm({
  projectId, summary, bill, onDone,
}: { projectId: string; summary: CostSummary; bill: Bill | null; onDone: (msg?: string) => void }) {
  const [kind, setKind] = useState<CostKind>(bill?.kind ?? "supplier_invoice");
  const [vendor, setVendor] = useState(bill?.vendor ?? "");
  const [number, setNumber] = useState(bill?.invoice_number ?? "");
  const [date, setDate] = useState(bill?.cost_date ?? "");
  const [amount, setAmount] = useState(bill ? String(bill.amount) : "");
  const [gst, setGst] = useState(bill ? String(bill.gst) : "");
  const [gstTouched, setGstTouched] = useState(!!bill);
  const [division, setDivision] = useState(bill?.division ?? "");
  const [what, setWhat] = useState(bill?.description ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const onAmount = (v: string) => {
    setAmount(v);
    // GST follows the amount at 5% until it's typed over (bills without GST exist).
    if (!gstTouched) setGst(v === "" || isNaN(Number(v)) ? "" : (Math.round(Number(v) * 5) / 100).toFixed(2));
  };

  const problem =
    !vendor.trim() ? "Say who the bill is from."
      : !date ? "Give the date on the bill."
        : date > today() ? "The bill's date is in the future."
          : amount === "" || isNaN(Number(amount)) || Number(amount) === 0 ? "Give the amount before GST."
            : gst !== "" && isNaN(Number(gst)) ? "GST must be a number."
              : null;

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      let docId = bill?.source_document_id ?? null;
      if (file) docId = (await uploadDocument(projectId, file, "invoice", undefined, true)).id;
      const body: BillInput = {
        kind, vendor: vendor.trim(), invoice_number: number.trim() || null, cost_date: date,
        amount: Number(amount), gst: gst === "" ? 0 : Number(gst), division: division || null,
        description: what.trim() || null, source_document_id: docId,
      };
      if (bill) await editBill(projectId, bill.id, body);
      else await addBill(projectId, body);
      onDone(`${bill ? "Saved" : "Added"} the ${vendor.trim()} bill.`);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const input = "rounded border border-slate-300 px-2 py-1 text-sm";
  return (
    <Box className="space-y-3 border-slate-900">
      <Label>{bill ? "Edit the bill" : "Add a bill"}</Label>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex min-w-0 flex-1 basis-56 flex-col gap-1">
          <span className="text-slate-600">From</span>
          <input list="cost-vendors" value={vendor} onChange={(e) => setVendor(e.target.value)} className={`min-w-0 ${input}`} />
          <datalist id="cost-vendors">{summary.vendors.map((v) => <option key={v} value={v} />)}</datalist>
        </label>
        <fieldset className="flex items-center gap-3">
          {(Object.keys(KIND_LABEL) as CostKind[]).map((k) => (
            <label key={k} className="flex items-center gap-1">
              <input type="radio" name="cost-kind" checked={kind === k} onChange={() => setKind(k)} />
              {KIND_LABEL[k]}
            </label>
          ))}
        </fieldset>
        <label className="flex flex-col gap-1">
          <span className="text-slate-600">Invoice no.</span>
          <input value={number} onChange={(e) => setNumber(e.target.value)} className={`w-32 ${input}`} />
        </label>
      </div>
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1">
          <span className="text-slate-600">Date on the bill</span>
          <input type="date" max={today()} value={date} onChange={(e) => setDate(e.target.value)} className={input} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-slate-600">Before GST</span>
          <input inputMode="decimal" value={amount} onChange={(e) => onAmount(e.target.value)} className={`w-32 text-right ${input}`} />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-slate-600">GST</span>
          <input inputMode="decimal" value={gst} onChange={(e) => { setGstTouched(true); setGst(e.target.value); }} className={`w-28 text-right ${input}`} />
        </label>
        <div className="pb-1 text-slate-600">
          Total {cents((Number(amount) || 0) + (Number(gst) || 0))}
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-slate-600">Division</span>
          <select value={division} onChange={(e) => setDivision(e.target.value)} className={`max-w-full ${input}`}>
            <option value="">Choose…</option>
            {summary.divisions.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label className="flex min-w-0 flex-1 basis-56 flex-col gap-1">
          <span className="text-slate-600">What for (optional)</span>
          <input value={what} onChange={(e) => setWhat(e.target.value)} className={`min-w-0 ${input}`} />
        </label>
      </div>
      <label className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-600">{bill?.source_document_id ? "Replace the file" : "Attach the bill"} (photo or PDF)</span>
        <input type="file" accept="image/*,application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="min-w-0 text-sm" />
      </label>
      {!division && <p className="m-0 text-xs text-orange-800">No division: it counts in Spent but not against any budget line.</p>}
      {err && <p className="m-0 text-sm text-red-700">{err}</p>}
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={save} disabled={busy || !!problem} className="rounded-md bg-slate-900 px-4 py-1.5 text-sm text-white disabled:opacity-50">
          {busy ? "Saving…" : bill ? "Save" : "Add the bill"}
        </button>
        <button type="button" onClick={() => onDone()} className="text-sm text-slate-600">Cancel</button>
        {problem && <span className="text-xs text-orange-800">{problem}</span>}
      </div>
    </Box>
  );
}

function BudgetPanel({
  projectId, replacing, onDone,
}: { projectId: string; replacing: boolean; onDone: (s?: CostSummary) => void }) {
  const [ests, setEsts] = useState<BudgetEstimate[] | null>(null);
  const [vid, setVid] = useState("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [coDiv, setCoDiv] = useState<Record<string, string>>({});
  const [p, setP] = useState<BudgetPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { budgetEstimates(projectId).then(setEsts).catch((e) => setErr(errorMessage(e))); }, [projectId]);
  useEffect(() => {
    if (!vid) { setP(null); return; }
    previewBudget(projectId, { estimate_version_id: vid, mapping, co_divisions: coDiv })
      .then((x) => { setP(x); setErr(null); })
      .catch((e) => { setP(null); setErr(errorMessage(e)); });
  }, [projectId, vid, mapping, coDiv]);

  const save = async () => {
    if (replacing && !confirm("Replace the current budget?")) return;
    setBusy(true);
    try {
      onDone(await saveBudget(projectId, { estimate_version_id: vid, mapping, co_divisions: coDiv }));
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const sel = "max-w-full rounded border border-slate-300 px-2 py-1 text-sm";
  return (
    <Box className="space-y-3 border-slate-900">
      <Label>{replacing ? "Reset the budget" : "Set the budget"}</Label>
      <p className="m-0 text-sm text-slate-600">
        Spreads the contract&apos;s base value across divisions in the same proportions as an estimate&apos;s direct costs,
        then adds each approved change order as its own line.
      </p>
      <label className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-600">Take the proportions from</span>
        <select value={vid} onChange={(e) => { setVid(e.target.value); setMapping({}); setCoDiv({}); }} className={sel}>
          <option value="">Choose an estimate…</option>
          {(ests ?? []).map((e) => (
            <option key={e.id} value={e.id}>{e.label ?? `v${e.version_number}`} (direct cost {fmtMoney(e.direct_cost)})</option>
          ))}
        </select>
      </label>
      {err && <p className="m-0 text-sm text-red-700">{err}</p>}
      {p && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr className="border-b border-slate-300">
                <th className="py-2 pr-3">In the estimate</th>
                <th className="px-3 py-2 text-right">Share</th>
                <th className="px-3 py-2 text-right">Budget</th>
                <th className="px-3 py-2">Division</th>
              </tr>
            </thead>
            <tbody>
              {p.categories.map((x) => (
                <tr key={x.name} className="border-b border-slate-100">
                  <td className="py-1.5 pr-3">{x.name}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{(x.share * 100).toFixed(1)}%</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{cents(x.amount)}</td>
                  <td className="px-3 py-1.5">
                    <select value={x.division ?? ""} onChange={(e) => setMapping((m) => ({ ...m, [x.name]: e.target.value }))}
                      className={`${sel} ${x.division ? "" : "border-orange-700"}`}>
                      <option value="">Pick a division…</option>
                      {p.divisions.map((d) => <option key={d} value={d}>{d}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
              {p.change_orders.map((x) => (
                <tr key={x.number} className="border-b border-slate-100">
                  <td className="py-1.5 pr-3">CO-{x.number} {x.title}</td>
                  <td className="px-3 py-1.5 text-right text-xs text-slate-500">approved</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{cents(x.amount)}</td>
                  <td className="px-3 py-1.5">
                    <select value={x.division ?? ""} onChange={(e) => setCoDiv((m) => ({ ...m, [String(x.number)]: e.target.value }))}
                      className={`${sel} ${x.division ? "" : "border-orange-700"}`}>
                      <option value="">Pick a division…</option>
                      {p.divisions.map((d) => <option key={d} value={d}>{d}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
              <tr>
                <td className="py-2 pr-3 font-semibold">Total</td>
                <td />
                <td className="px-3 py-2 text-right font-semibold tabular-nums">{cents(p.total)}</td>
                <td className="px-3 py-2 text-xs text-slate-500">contract {cents(p.contract_base)} + approved change orders</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={save} disabled={!p?.complete || busy}
          className="rounded-md bg-slate-900 px-4 py-1.5 text-sm text-white disabled:opacity-50">
          {busy ? "Saving…" : "Save the budget"}
        </button>
        <button type="button" onClick={() => onDone()} className="text-sm text-slate-600">Cancel</button>
        {p && !p.complete && <span className="text-xs text-orange-800">Pick a division for each line marked in orange.</span>}
      </div>
    </Box>
  );
}
