"use client";

import { useSearchParams } from "next/navigation";
import { use, useCallback, useEffect, useMemo, useState } from "react";

import { Box, Label } from "@/components/kit";
import { WorkspaceError } from "@/components/WorkspaceError";
import { changeOrderPdf, submitChangeOrder } from "@/lib/changeOrders";
import { fmtMoney } from "@/lib/fmt";
import { generateInvoicePdf, sendInvoice } from "@/lib/invoices";
import { getMoney, prepareNextInvoice, remindChangeOrder, type MoneySummary } from "@/lib/money";

import ChangeOrdersPage from "../change-orders/page";
import InvoicesPage from "../invoices/page";
import Costs from "./Costs";

/**
 * Money — round-1 wireframe. Summary: contract, change orders, invoiced,
 * paid, owing; the change orders and invoices with what each is waiting on;
 * and the next payment-schedule milestone ready to prepare. Change orders and
 * Invoices are the full pages (create, edit, record payment), unchanged.
 *
 * Every send here asks first: these go to the client.
 */
type View = "summary" | "change_orders" | "invoices" | "costs";
const VIEWS: View[] = ["summary", "change_orders", "invoices", "costs"];

export default function MoneyPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: projectId } = use(params);
  // ?view=costs opens that tab (Ask's "are we on budget?" links there).
  const asked = useSearchParams().get("view") as View | null;
  const [view, setView] = useState<View>(asked && VIEWS.includes(asked) ? asked : "summary");
  // The embedded pages read their id from a params promise; it must be stable
  // across renders or `use()` would suspend again on every one.
  const embedded = useMemo(() => Promise.resolve({ id: projectId }), [projectId]);

  return (
    <div className="space-y-4">
      <div className="inline-flex overflow-hidden rounded-md border border-slate-900 text-[13px]" role="group" aria-label="Money view">
        {(
          [
            ["summary", "Summary"],
            ["change_orders", "Change orders"],
            ["invoices", "Invoices"],
            ["costs", "Costs"],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            type="button"
            onClick={() => setView(k)}
            aria-pressed={view === k}
            className={`px-3 py-1.5 ${view === k ? "bg-slate-900 text-white" : ""}`}
          >
            {label}
          </button>
        ))}
      </div>
      {view === "summary" && <Summary projectId={projectId} go={setView} />}
      {view === "change_orders" && <ChangeOrdersPage params={embedded} />}
      {view === "invoices" && <InvoicesPage params={embedded} />}
      {view === "costs" && <Costs projectId={projectId} />}
    </div>
  );
}

const CO_STATUS: Record<string, string> = {
  draft: "Draft",
  approved: "Approved by client",
  rejected: "Rejected by client",
};

const INV_STATUS: Record<string, { label: string; cls: string }> = {
  draft: { label: "Draft", cls: "border-slate-400 text-slate-700" },
  sent: { label: "Sent", cls: "border-slate-600 text-slate-800" },
  partial: { label: "Part paid", cls: "border-orange-700 text-orange-800" },
  overdue: { label: "Overdue", cls: "border-red-700 text-red-800" },
  paid: { label: "Paid", cls: "border-emerald-700 text-emerald-800" },
};

function Summary({ projectId, go }: { projectId: string; go: (v: View) => void }) {
  const [m, setM] = useState<MoneySummary | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(() => {
    getMoney(projectId).then(setM).catch(setErr);
  }, [projectId]);
  useEffect(load, [load]);

  const act = async (key: string, fn: () => Promise<string | void>) => {
    setBusy(key);
    setErr(null);
    setNote(null);
    try {
      const msg = await fn();
      if (msg) setNote(msg);
      load();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  if (!m) return err != null ? <WorkspaceError error={err} onRetry={load} /> : <p className="text-sm text-slate-500">Loading…</p>;

  const kpis: [string, number | null, string?][] = [
    [
      "Contract",
      m.contract?.with_gst ?? null,
      m.contract ? (m.contract.source === "contract" ? "with GST" : `from estimate v${m.contract.version_number}, with GST`) : "none yet",
    ],
    ["Change orders", m.change_orders.approved_with_gst, "approved, with GST"],
    ["Invoiced", m.invoiced, "sent to the client"],
    ["Paid", m.paid],
    ["Owing to us", m.owing],
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {kpis.map(([label, v, sub], i) => (
          <Box key={label} className={`p-4 ${i === 4 && m.owing > 0 ? "border-orange-700" : ""}`}>
            <Label>{label}</Label>
            <div className={`mt-1 text-xl font-bold tabular-nums ${i === 4 && m.owing > 0 ? "text-orange-800" : ""}`}>
              {i === 1 && v ? "+" : ""}
              {fmtMoney(v)}
            </div>
            {sub && <div className="text-xs text-slate-500">{sub}</div>}
          </Box>
        ))}
      </div>
      {m.revised_contract_with_gst != null && m.change_orders.approved_with_gst > 0 && (
        <p className="m-0 text-[13px] text-slate-600">
          Contract with approved change orders: {fmtMoney(m.revised_contract_with_gst)} with GST.
        </p>
      )}

      {err != null && <WorkspaceError error={err} />}
      {note && <p className="m-0 text-[13px] text-emerald-800">{note}</p>}

      <Box className="overflow-x-auto p-0">
        <div className="flex flex-wrap items-center justify-between gap-2 px-5 pb-2 pt-4">
          <Label>Change orders</Label>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => go("change_orders")} className="rounded-md border border-slate-900 px-3 py-1.5 text-sm">
              Draft one from a document
            </button>
            <button type="button" onClick={() => go("change_orders")} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white">
              + New change order
            </button>
          </div>
        </div>
        <table className="w-full min-w-[620px] text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
            <tr className="border-b border-slate-300">
              <th className="px-5 py-2">#</th>
              <th className="px-3 py-2">What</th>
              <th className="px-3 py-2 text-right">Amount before GST</th>
              <th className="px-3 py-2">Adds to schedule</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {m.change_orders.items.map((c) => (
              <tr key={c.id} className="border-b border-slate-100">
                <td className="px-5 py-2.5">CO-{c.number}</td>
                <td className="max-w-md px-3 py-2.5">
                  <div className="truncate" title={c.title}>
                    {c.title}
                  </div>
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums">+{fmtMoney(c.amount)}</td>
                <td className="px-3 py-2.5">{c.days == null ? "—" : c.days === 0 ? "None" : `+${c.days} day${c.days === 1 ? "" : "s"}`}</td>
                <td className="px-3 py-2.5 text-[13px]">
                  {c.status === "pending" ? (
                    <span className="text-orange-800">
                      Waiting on client{c.waiting_days != null ? ` · ${c.waiting_days} day${c.waiting_days === 1 ? "" : "s"}` : " (sent before this was recorded)"}
                    </span>
                  ) : (
                    <span className={c.status === "approved" ? "text-emerald-800" : ""}>{CO_STATUS[c.status] ?? c.status}</span>
                  )}
                </td>
                <td className="px-3 py-2.5">
                  <div className="flex flex-wrap gap-3 text-[13px]">
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() =>
                        act(`co-pdf-${c.id}`, async () => {
                          const r = await changeOrderPdf(projectId, c.id);
                          window.open(r.download_url, "_blank", "noopener");
                        })
                      }
                      className="text-blue-700 disabled:opacity-50"
                    >
                      PDF
                    </button>
                    {c.status === "pending" && (
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() =>
                          confirm(`Send the client a reminder about CO-${c.number}?`) &&
                          act(`co-remind-${c.id}`, async () => {
                            await remindChangeOrder(projectId, c.id);
                            return `Reminder sent for CO-${c.number}.`;
                          })
                        }
                        className="text-blue-700 disabled:opacity-50"
                      >
                        Remind
                      </button>
                    )}
                    {c.status === "draft" && (
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() =>
                          confirm(`Send CO-${c.number} to the client for approval?`) &&
                          act(`co-send-${c.id}`, async () => {
                            await submitChangeOrder(projectId, c.id);
                            return `CO-${c.number} sent to the client.`;
                          })
                        }
                        className="text-blue-700 disabled:opacity-50"
                      >
                        Send to client
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {m.change_orders.items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-5 py-4 text-slate-500">
                  No change orders.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Box>

      <Box className="overflow-x-auto p-0">
        <div className="flex flex-wrap items-center justify-between gap-2 px-5 pb-2 pt-4">
          <Label>Invoices</Label>
          <button type="button" onClick={() => go("invoices")} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white">
            + New invoice
          </button>
        </div>
        <table className="w-full min-w-[620px] text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
            <tr className="border-b border-slate-300">
              <th className="px-5 py-2">#</th>
              <th className="px-3 py-2">For</th>
              <th className="px-3 py-2 text-right">Total</th>
              <th className="px-3 py-2 text-right">Paid</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {m.invoices.map((inv) => {
              const st = INV_STATUS[inv.status] ?? { label: inv.status, cls: "border-slate-400" };
              const open = ["sent", "partial", "overdue"].includes(inv.status);
              return (
                <tr key={inv.id} className="border-b border-slate-100">
                  <td className="whitespace-nowrap px-5 py-2.5">{inv.number}</td>
                  <td className="max-w-md px-3 py-2.5">
                    <div className="truncate" title={inv.for}>
                      {inv.for}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{fmtMoney(inv.total)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{fmtMoney(inv.paid)}</td>
                  <td className="px-3 py-2.5">
                    <span className={`rounded border px-1.5 py-0.5 text-xs ${st.cls}`}>{st.label}</span>
                    {inv.status === "overdue" && inv.due && <span className="ml-1 text-xs text-red-800">due {inv.due}</span>}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap gap-3 text-[13px]">
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() =>
                          act(`inv-pdf-${inv.id}`, async () => {
                            const r = await generateInvoicePdf(inv.id);
                            window.open(r.download_url, "_blank", "noopener");
                          })
                        }
                        className="text-blue-700 disabled:opacity-50"
                      >
                        PDF
                      </button>
                      {open && (
                        <button type="button" onClick={() => go("invoices")} className="text-blue-700">
                          Record payment
                        </button>
                      )}
                      {open && (
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() =>
                            confirm(`Email ${inv.number} to the client again as a reminder?`) &&
                            act(`inv-remind-${inv.id}`, async () => {
                              const r = await sendInvoice(inv.id);
                              return `${inv.number} sent again to ${r.sent_to}.`;
                            })
                          }
                          className="text-blue-700 disabled:opacity-50"
                        >
                          Remind
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
            {m.next_milestone ? (
              <tr className="bg-slate-50">
                <td className="px-5 py-2.5 text-slate-600">Next</td>
                <td className="px-3 py-2.5 text-slate-700">
                  {m.next_milestone.milestone}
                  {m.next_milestone.pct != null ? ` · ${m.next_milestone.pct}%` : ""}
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums">{fmtMoney(m.next_milestone.amount_with_gst)}</td>
                <td className="px-3 py-2.5 text-right">—</td>
                <td className="px-3 py-2.5 text-xs text-slate-600">Not yet</td>
                <td className="px-3 py-2.5">
                  <button
                    type="button"
                    disabled={busy !== null || m.next_milestone.amount_before_gst == null}
                    onClick={() =>
                      act("prepare", async () => {
                        const r = await prepareNextInvoice(projectId);
                        return `Draft ${r.number} prepared for "${r.milestone}". Check it under Invoices before sending.`;
                      })
                    }
                    className="rounded-md border border-slate-900 px-2.5 py-1 text-[13px] disabled:opacity-50"
                  >
                    {busy === "prepare" ? "Preparing…" : "Prepare"}
                  </button>
                </td>
              </tr>
            ) : (
              <tr>
                <td colSpan={6} className="px-5 py-3 text-[13px] text-slate-500">
                  {m.schedule_count ? "Every milestone on the payment schedule has an invoice." : "No payment schedule on this project."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Box>
    </div>
  );
}
