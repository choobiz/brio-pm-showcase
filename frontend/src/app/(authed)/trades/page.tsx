"use client";

import { useEffect, useState } from "react";

import { Card } from "@/components/ui/card";
import {
  type Trade, type TradeSummary, type TradeCategory, type TradeInput,
  TRADE_CATEGORY_LABELS,
  TRADE_DIVISION, TRADE_CATEGORIES,
  listTrades, getTradeSummary, createTrade, updateTrade, confirmTrade, deleteTrade,
} from "@/lib/trades";

const CAT_CLS = "bg-slate-100 text-slate-700";

const emptyForm: TradeInput = {
  company_name: "", contact_name: "", trade_category: "other",
  email: "", phone: "", website: "", notes: "",
};

export default function TradeLibraryPage() {
  const [rows, setRows] = useState<Trade[]>([]);
  const [sum, setSum] = useState<TradeSummary | null>(null);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [statusF, setStatusF] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // form (create or edit)
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState<TradeInput>(emptyForm);

  const load = () => {
    listTrades({ q: q || undefined, category: category || undefined, status: statusF || undefined })
      .then(setRows).catch((e) => setErr(String(e)));
    getTradeSummary().then(setSum).catch(() => {});
  };
  useEffect(() => {
    const t = setTimeout(load, q ? 250 : 0); // debounce search
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, category, statusF]);

  function openCreate() {
    setEditId(null); setForm(emptyForm); setShowForm(true);
  }
  function openEdit(t: Trade) {
    setEditId(t.id);
    setForm({
      company_name: t.company_name ?? "", contact_name: t.contact_name ?? "",
      trade_category: t.trade_category, email: t.email ?? "", phone: t.phone ?? "",
      website: t.website ?? "", notes: t.notes ?? "", rating: t.rating,
    });
    setShowForm(true);
  }

  async function save() {
    if (!form.company_name && !form.contact_name) { setErr("Company or contact name required"); return; }
    setBusy(true); setErr(null);
    try {
      if (editId) await updateTrade(editId, form);
      else await createTrade(form);
      setShowForm(false); setForm(emptyForm); setEditId(null);
      load();
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  async function confirm(id: string) {
    setBusy(true); try { await confirmTrade(id); load(); } finally { setBusy(false); }
  }
  async function archive(t: Trade) {
    setBusy(true);
    try { await updateTrade(t.id, { status: t.status === "archived" ? "active" : "archived" }); load(); }
    finally { setBusy(false); }
  }
  async function remove(id: string) {
    if (typeof window !== "undefined" && !window.confirm("Delete this trade from the library?")) return;
    setBusy(true); try { await deleteTrade(id); load(); } finally { setBusy(false); }
  }

  const inp = "w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500";
  const sc = sum?.status_counts ?? {};
  const unconfirmed = sc["unconfirmed"] ?? 0;

  return (
    <div className="space-y-5 max-w-5xl">
      <header className="flex items-baseline justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-xl font-semibold">Trade Library</h1>
          <p className="text-sm text-slate-600 mt-0.5">
            Cross-project directory of trades, subcontractors &amp; suppliers.
            Trades mentioned in project Conversations are auto-added here for review.
          </p>
        </div>
        <button onClick={openCreate}
          className="rounded-md bg-[var(--color-brio-primary)] px-3 py-1.5 text-sm font-medium text-white hover:opacity-90">
          + Add trade
        </button>
      </header>

      {err && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}

      {/* Status + search */}
      <div className="flex flex-wrap items-center gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, email, phone, notes…"
          className={`${inp} max-w-xs`} />
        <select value={category} onChange={(e) => setCategory(e.target.value)} className={`${inp} max-w-[12rem]`}>
          <option value="">All categories</option>
          {TRADE_CATEGORIES.map((c) => <option key={c} value={c}>{TRADE_CATEGORY_LABELS[c]}</option>)}
        </select>
        <div className="flex gap-1.5 ml-auto">
          {[["", "All"], ["active", "Active"], ["archived", "Archived"]].map(([v, label]) => (
            <button key={v} onClick={() => setStatusF(v)}
              className={`rounded-full px-2.5 py-1 text-xs ${statusF === v ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-600"}`}>
              {label}
            </button>
          ))}
          {unconfirmed > 0 && (
            <button onClick={() => setStatusF("unconfirmed")}
              className={`rounded-full px-2.5 py-1 text-xs font-semibold ${statusF === "unconfirmed" ? "bg-amber-500 text-white" : "bg-amber-100 text-amber-800"}`}>
              Unconfirmed {unconfirmed}
            </button>
          )}
        </div>
      </div>

      {/* Form */}
      {showForm && (
        <Card className="p-4 space-y-2">
          <div className="text-sm font-medium">{editId ? "Edit trade" : "New trade"}</div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <input value={form.company_name ?? ""} onChange={(e) => setForm({ ...form, company_name: e.target.value })} placeholder="Company name" className={inp} />
            <input value={form.contact_name ?? ""} onChange={(e) => setForm({ ...form, contact_name: e.target.value })} placeholder="Contact person" className={inp} />
            <select value={form.trade_category} onChange={(e) => setForm({ ...form, trade_category: e.target.value })} className={inp}>
              {TRADE_CATEGORIES.map((c) => <option key={c} value={c}>{TRADE_CATEGORY_LABELS[c]}</option>)}
            </select>
            <input value={form.website ?? ""} onChange={(e) => setForm({ ...form, website: e.target.value })} placeholder="Website" className={inp} />
            <input value={form.email ?? ""} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="Email" className={inp} />
            <input value={form.phone ?? ""} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="Phone" className={inp} />
          </div>
          <textarea value={form.notes ?? ""} onChange={(e) => setForm({ ...form, notes: e.target.value })} rows={2} placeholder="Notes" className={inp} />
          <div className="flex gap-2">
            <button onClick={save} disabled={busy}
              className="rounded-md bg-[var(--color-brio-primary)] px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50">
              {busy ? "Saving…" : editId ? "Save changes" : "Add to library"}
            </button>
            <button onClick={() => { setShowForm(false); setEditId(null); }} className="rounded-md px-4 py-1.5 text-sm text-slate-600 hover:bg-slate-100">Cancel</button>
          </div>
        </Card>
      )}

      {/* List */}
      <div className="space-y-2">
        {rows.length === 0 && <div className="text-sm text-slate-400">No trades yet.</div>}
        {rows.map((t) => (
          <Card key={t.id} className={`p-3.5 ${t.status === "unconfirmed" ? "border-amber-300 bg-amber-50/40" : ""}`}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${CAT_CLS}`}>
                    {TRADE_DIVISION[t.trade_category as TradeCategory] ? `${TRADE_DIVISION[t.trade_category as TradeCategory]} · ` : ""}
                    {TRADE_CATEGORY_LABELS[t.trade_category as TradeCategory] ?? t.trade_category}
                  </span>
                  <span className="text-sm font-semibold text-slate-800 truncate">
                    {t.company_name || t.contact_name || "(unnamed)"}
                  </span>
                  {t.company_name && t.contact_name && <span className="text-xs text-slate-500">· {t.contact_name}</span>}
                  {t.status === "unconfirmed" && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800">unconfirmed</span>}
                  {t.status === "archived" && <span className="rounded bg-slate-200 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">archived</span>}
                  {t.source === "communication" && <span className="rounded bg-sky-50 px-1.5 py-0.5 text-[10px] text-sky-700" title="Auto-extracted from a project conversation">from conversation</span>}
                </div>
                <div className="text-xs text-slate-500 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                  {t.email && <span>✉ {t.email}</span>}
                  {t.phone && <span>☎ {t.phone}</span>}
                  {t.website && <span>🔗 {t.website}</span>}
                </div>
                {t.notes && <p className="text-sm text-slate-600 mt-1.5">{t.notes}</p>}
              </div>
              <div className="shrink-0 flex items-center gap-2 text-xs">
                {t.status === "unconfirmed" && (
                  <button onClick={() => confirm(t.id)} disabled={busy}
                    className="rounded bg-emerald-600 px-2 py-1 font-medium text-white hover:opacity-90">Confirm</button>
                )}
                <button onClick={() => openEdit(t)} className="text-slate-500 hover:text-slate-800">Edit</button>
                <button onClick={() => archive(t)} className="text-slate-400 hover:text-slate-700">{t.status === "archived" ? "Restore" : "Archive"}</button>
                <button onClick={() => remove(t.id)} className="text-slate-300 hover:text-red-600" title="Delete">✕</button>
              </div>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
