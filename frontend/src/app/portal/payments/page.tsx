"use client";

import { useEffect, useState } from "react";

import { usePortal } from "../portalCtx";
import {
  getPayments, getChangeOrders, decideChangeOrder,
  getInvoices, getInvoicePdf,
  type Payments, type PortalChangeOrder, type PortalInvoice,
} from "@/lib/portal";

function money(n: number): string {
  const s = Math.abs(n).toLocaleString("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 });
  return n < 0 ? `−${s}` : s;
}

const STATUS: Record<string, { label: string; color: string; bg: string }> = {
  paid: { label: "Paid", color: "#0a7d4f", bg: "#e7f6ee" },
  partial: { label: "Partial", color: "#8a5a00", bg: "#fbf1dc" },
  due: { label: "Upcoming", color: "#93a0ad", bg: "#f1efec" },
};

const INV_STATUS: Record<string, { label: string; color: string; bg: string }> = {
  paid: { label: "Paid", color: "#0a7d4f", bg: "#e7f6ee" },
  partial: { label: "Partial", color: "#8a5a00", bg: "#fbf1dc" },
  sent: { label: "Due", color: "#0068A0", bg: "#e6f1f7" },
  overdue: { label: "Overdue", color: "#b42318", bg: "#fdeceb" },
};

export default function PaymentsPage() {
  const { project, loading } = usePortal();
  const [p, setP] = useState<Payments | null>(null);
  const [cos, setCos] = useState<PortalChangeOrder[]>([]);
  const [invs, setInvs] = useState<PortalInvoice[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const reload = (pid: string) => {
    getPayments(pid).then(setP).catch(() => setP(null));
    getChangeOrders(pid).then(setCos).catch(() => setCos([]));
    getInvoices(pid).then(setInvs).catch(() => setInvs([]));
  };

  async function openInvoice(iid: string) {
    if (!project) return;
    setBusy(iid);
    try {
      const r = await getInvoicePdf(project.id, iid);
      window.open(r.download_url, "_blank", "noopener");
    } catch {
      /* leave silently; the button re-enables */
    } finally {
      setBusy(null);
    }
  }
  useEffect(() => {
    if (project) reload(project.id);
  }, [project]);

  async function decide(coId: string, decision: "approve" | "reject") {
    if (decision === "reject" && !confirm("Decline this change?")) return;
    setBusy(coId);
    try {
      await decideChangeOrder(coId, decision);
      if (project) reload(project.id);
    } finally {
      setBusy(null);
    }
  }

  if (loading || !project) return <div className="h-40" />;

  const total = p?.revised_total ?? p?.contract_total ?? 0;
  const pct = total ? Math.min(100, Math.round(((p?.paid_to_date ?? 0) / total) * 100)) : 0;
  const pending = cos.filter((c) => c.status === "pending");

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold" style={{ color: "#1D3146" }}>Payments</h1>

      {/* Summary */}
      <div className="rounded-2xl border bg-white p-5 shadow-sm" style={{ borderColor: "#ece8e2" }}>
        <div className="flex items-baseline justify-between">
          <span className="text-sm text-slate-500">Paid to date</span>
          <span className="text-2xl font-semibold" style={{ color: "#0068A0" }}>
            {money(p?.paid_to_date ?? 0)}
          </span>
        </div>
        <div className="mt-3 h-2 w-full overflow-hidden rounded-full" style={{ background: "#ece8e2" }}>
          <div className="h-full rounded-full" style={{ width: `${pct}%`, background: "#0068A0" }} />
        </div>
        <div className="mt-2 flex justify-between text-xs text-slate-400">
          <span>{p?.change_orders_total ? "Revised total" : "Contract total"} {money(total)}</span>
          <span>Remaining {money(p?.remaining ?? 0)}</span>
        </div>
        {!!p?.change_orders_total && (
          <div className="mt-1 text-[11px] text-slate-400">
            Includes {money(p.change_orders_total)} in approved changes (contract {money(p.contract_total)}).
          </div>
        )}
      </div>

      {/* Invoices — issued bills the client can view/download */}
      {invs.length > 0 && (
        <div className="space-y-2">
          <div className="text-[11px] uppercase tracking-wider text-slate-400">Invoices</div>
          {invs.map((inv) => {
            const s = INV_STATUS[inv.status] ?? INV_STATUS.sent;
            return (
              <div key={inv.id} className="rounded-xl border bg-white px-4 py-3 shadow-sm" style={{ borderColor: "#ece8e2" }}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-slate-800">{inv.number}</div>
                    {inv.description && <div className="text-xs text-slate-500 mt-0.5">{inv.description}</div>}
                    {inv.date && <div className="text-xs text-slate-400 mt-0.5">{inv.date}</div>}
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-sm font-semibold text-slate-800">{money(inv.total)}</div>
                    <span className="mt-0.5 inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold"
                          style={{ color: s.color, background: s.bg }}>
                      {s.label}
                    </span>
                  </div>
                </div>
                <div className="mt-2 flex items-center justify-between">
                  <span className="text-xs text-slate-400">
                    {inv.balance > 0 ? `Balance ${money(inv.balance)}` : "Paid in full"}
                  </span>
                  <button onClick={() => openInvoice(inv.id)} disabled={busy === inv.id}
                          className="rounded-lg border px-3 py-1.5 text-xs font-medium text-slate-600 disabled:opacity-50"
                          style={{ borderColor: "#ece8e2" }}>
                    {busy === inv.id ? "…" : "View PDF"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Change orders — pending ones the client can approve/decline */}
      {cos.length > 0 && (
        <div className="space-y-2">
          <div className="text-[11px] uppercase tracking-wider text-slate-400">Change orders</div>
          {pending.length > 0 && (
            <p className="text-xs text-amber-700">
              {pending.length} change{pending.length > 1 ? "s" : ""} awaiting your approval.
            </p>
          )}
          {cos.map((c) => (
            <div key={c.id} className="rounded-xl border bg-white px-4 py-3 shadow-sm" style={{ borderColor: "#ece8e2" }}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-slate-800">{c.reference} — {c.title}</div>
                  {c.description && <div className="text-xs text-slate-500 mt-0.5">{c.description}</div>}
                  <div className="text-sm text-slate-700 mt-1">
                    {money(c.cost_impact_incl_gst)} incl. GST
                    {c.schedule_impact_days !== 0 && (
                      <span> · {c.schedule_impact_days > 0 ? "+" : ""}{c.schedule_impact_days} days</span>
                    )}
                  </div>
                </div>
                {c.status !== "pending" && (
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                    c.status === "approved" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>
                    {c.status === "approved" ? "Approved" : "Declined"}
                  </span>
                )}
              </div>
              {c.status === "pending" && (
                <div className="mt-2 flex gap-2">
                  <button onClick={() => decide(c.id, "approve")} disabled={busy === c.id}
                          className="flex-1 rounded-lg py-2 text-sm font-semibold text-white disabled:opacity-50"
                          style={{ background: "#0a7d4f" }}>
                    {busy === c.id ? "…" : "Approve"}
                  </button>
                  <button onClick={() => decide(c.id, "reject")} disabled={busy === c.id}
                          className="rounded-lg border px-4 py-2 text-sm font-medium text-slate-600 disabled:opacity-50"
                          style={{ borderColor: "#ece8e2" }}>
                    Decline
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Draw schedule */}
      <div className="space-y-2">
        <div className="text-[11px] uppercase tracking-wider text-slate-400">Payment schedule</div>
        {p?.draws.map((d, i) => {
          const s = STATUS[d.status] ?? STATUS.due;
          return (
            <div key={i} className="flex items-center justify-between rounded-xl border bg-white px-4 py-3 shadow-sm"
                 style={{ borderColor: "#ece8e2" }}>
              <div className="pr-3">
                <div className="text-sm font-medium text-slate-800">{d.milestone}</div>
                <div className="text-xs text-slate-400">{d.pct}% of contract</div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-sm font-semibold text-slate-800">{money(d.amount)}</div>
                <span className="mt-0.5 inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold"
                      style={{ color: s.color, background: s.bg }}>
                  {s.label}
                </span>
              </div>
            </div>
          );
        })}
        {p && p.draws.length === 0 && (
          <p className="text-sm text-slate-400">Your payment schedule will appear here once the contract is set.</p>
        )}
      </div>

      <p className="text-xs text-slate-400">
        Amounts include GST. Please refer to your contract for full payment terms.
      </p>
    </div>
  );
}
