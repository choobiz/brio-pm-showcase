"use client";

import { use, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { listChangeOrders, type ChangeOrder } from "@/lib/changeOrders";
import {
  INVOICE_TYPES,
  createInvoice,
  newIdempotencyKey,
  seedInvoiceFromEstimate,
  seedInvoiceFromChangeOrder,
  generateInvoicePdf,
  getInvoice,
  getShareToken,
  listInvoices,
  listPayments,
  money,
  deleteInvoice,
  recordPayment,
  sendInvoice,
  updateInvoice,
  type InvoiceStatus,
  type InvoicePayload,
  type InvoiceRead,
  type InvoiceType,
  type PaymentRead,
} from "@/lib/invoices";

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function InvoicesPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: projectId } = use(params);
  const [invoices, setInvoices] = useState<InvoiceRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setInvoices(await listInvoices(projectId));
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (selectedId) {
    return (
      <InvoiceDetail
        invoiceId={selectedId}
        onBack={() => {
          setSelectedId(null);
          refresh();
        }}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>
          Invoices
        </h1>
        <Button onClick={() => setShowCreate(true)}>New Invoice</Button>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <Card className="p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Invoices ({invoices.length})
        </h2>
        {loading ? (
          <div className="mt-3 text-sm text-slate-400">Loading…</div>
        ) : invoices.length === 0 ? (
          <div className="mt-3 text-sm text-slate-400">No invoices yet.</div>
        ) : (
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-2">Number</th>
                <th className="py-2">Type</th>
                <th className="py-2 text-right">Total</th>
                <th className="py-2 text-right">Paid</th>
                <th className="py-2">Status</th>
                <th className="py-2 w-20"></th>
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => (
                <tr key={inv.id} className="border-t border-slate-100">
                  <td className="py-2 font-mono text-xs">{inv.number}</td>
                  <td className="py-2 text-slate-500">{inv.type}</td>
                  <td className="py-2 text-right">
                    {money(inv.payload.total ?? 0)}
                  </td>
                  <td className="py-2 text-right text-emerald-600">
                    {money(inv.paid_amount)}
                  </td>
                  <td className="py-2">
                    <StatusBadge status={inv.status} />
                  </td>
                  <td className="py-2 text-right">
                    <Button variant="ghost" onClick={() => setSelectedId(inv.id)}>
                      Open
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {showCreate && (
        <InvoiceModal
          projectId={projectId}
          existing={null}
          onClose={() => setShowCreate(false)}
          onCreated={(id) => {
            setShowCreate(false);
            setSelectedId(id);
          }}
        />
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const cls: Record<string, string> = {
    paid: "bg-emerald-100 text-emerald-800",
    partial: "bg-amber-100 text-amber-800",
    sent: "bg-blue-100 text-blue-800",
    overdue: "bg-red-100 text-red-800",
    draft: "bg-slate-100 text-slate-600",
    void: "bg-slate-100 text-slate-600",
  };
  return (
    <span
      className={`px-2 py-0.5 text-xs font-medium uppercase ${cls[status] ?? cls.draft}`}
    >
      {status}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Create modal
// ---------------------------------------------------------------------------

interface Row {
  [key: string]: string;
}

function InvoiceModal({
  projectId,
  existing,
  onClose,
  onCreated,
}: {
  projectId: string;
  existing?: InvoiceRead | null;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const ep = existing?.payload;
  const [type, setType] = useState<InvoiceType>(existing?.type ?? "progress");
  const [date, setDate] = useState(ep?.date ?? new Date().toISOString().slice(0, 10));
  const [terms, setTerms] = useState(ep?.terms ?? "Net 7");
  const [description, setDescription] = useState(ep?.description ?? "");
  const [notes, setNotes] = useState(ep?.notes ?? "");
  const [milestone, setMilestone] = useState(ep?.milestone ?? "");
  const [coNumber, setCoNumber] = useState(ep?.changeOrderNumber ?? "");
  const [reason, setReason] = useState(ep?.reason ?? "");
  const [retainerTerms, setRetainerTerms] = useState(ep?.retainerTerms ?? "");
  const [materialMarkup, setMaterialMarkup] = useState(ep?.materialMarkup ?? 15);
  const [lineItems, setLineItems] = useState<Row[]>(
    ep?.lineItems?.map((li) => ({
      description: li.description ?? "",
      percentage: li.percentage != null ? String(li.percentage) : "",
      amount: li.amount != null ? String(li.amount) : "",
      labor: li.labor != null ? String(li.labor) : "",
      materials: li.materials != null ? String(li.materials) : "",
    })) ?? [{ description: "", amount: "" }],
  );
  const [labor, setLabor] = useState<Row[]>(
    ep?.labor?.map((l) => ({
      worker: l.worker ?? "",
      description: l.description ?? "",
      hours: l.hours != null ? String(l.hours) : "",
      rate: l.rate != null ? String(l.rate) : "",
    })) ?? [{ worker: "", description: "", hours: "", rate: "" }],
  );
  const [materials, setMaterials] = useState<Row[]>(
    ep?.materials?.map((m) => ({
      item: m.item ?? "",
      supplier: m.supplier ?? "",
      qty: m.qty != null ? String(m.qty) : "",
      unitCost: m.unitCost != null ? String(m.unitCost) : "",
    })) ?? [{ item: "", supplier: "", qty: "", unitCost: "" }],
  );
  const [adjustments, setAdjustments] = useState<Row[]>(
    ep?.adjustments?.map((a) => ({
      description: a.description ?? "",
      amount: a.amount != null ? String(a.amount) : "",
    })) ?? [],
  );
  // Generated once per create form and REUSED on every retry. That is the whole
  // mechanism: a retry after a lost response must present the same key so the
  // server returns the invoice it already made instead of taking a second
  // number out of the sequence. Regenerating it per submit would look correct
  // and protect nothing.
  const [idempotencyKey] = useState(() => newIdempotencyKey());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const [seedNote, setSeedNote] = useState<string | null>(null);
  // Provenance. Carried onto the invoice so the backend can refuse to bill an
  // estimate version that has since been superseded.
  const [sourceEstimate, setSourceEstimate] =
    useState<InvoicePayload["sourceEstimate"]>(ep?.sourceEstimate);
  const [sourceChangeOrder, setSourceChangeOrder] =
    useState<InvoicePayload["sourceChangeOrder"]>(ep?.sourceChangeOrder);
  // The project's change orders, for the change-order seed picker. Loaded once
  // the type is change_order rather than on mount — every other invoice type
  // has no use for them.
  const [cos, setCos] = useState<ChangeOrder[] | null>(null);
  const [coId, setCoId] = useState<string>(ep?.sourceChangeOrder?.changeOrderId ?? "");
  const [seedWarnings, setSeedWarnings] = useState<string[]>([]);

  useEffect(() => {
    if (type !== "change_order" || cos !== null) return;
    listChangeOrders(projectId)
      .then((rows) => {
        const billable = rows.filter((c) => c.status !== "void");
        setCos(billable);
        if (!coId && billable.length > 0) {
          // Default to the most recent approved order, else the most recent.
          const approved = [...billable].reverse().find((c) => c.status === "approved");
          setCoId((approved ?? billable[billable.length - 1]).id);
        }
      })
      .catch(() => setCos([]));
  }, [type, cos, coId, projectId]);

  const seedFromChangeOrder = async () => {
    if (!coId) return;
    setSeeding(true);
    setErr(null);
    setSeedNote(null);
    setSeedWarnings([]);
    try {
      const seed = await seedInvoiceFromChangeOrder(coId);
      setLineItems(
        (seed.payload.lineItems ?? []).map((li) => ({
          description: li.description ?? "",
          percentage: "",
          amount: li.amount != null ? String(li.amount) : "",
          labor: "",
          materials: "",
        })),
      );
      if (seed.payload.description) setDescription(seed.payload.description);
      if (seed.payload.notes) setNotes(seed.payload.notes);
      if (seed.payload.changeOrderNumber) setCoNumber(seed.payload.changeOrderNumber);
      if (seed.payload.reason) setReason(seed.payload.reason);
      setSourceChangeOrder(seed.payload.sourceChangeOrder);
      setSeedWarnings(seed.warnings ?? []);
      setSeedNote(
        `Filled from ${seed.change_order_reference ?? "change order"} — subtotal ` +
          `${seed.payload.subtotal?.toLocaleString("en-CA", { style: "currency", currency: "CAD" })}.`,
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSeeding(false);
    }
  };

  const seedFromEstimate = async () => {
    setSeeding(true);
    setErr(null);
    setSeedNote(null);
    try {
      const seed = await seedInvoiceFromEstimate(projectId, type);
      setLineItems(
        (seed.payload.lineItems ?? []).map((li) => ({
          description: li.description ?? "",
          percentage: "",
          amount: li.amount != null ? String(li.amount) : "",
          labor: "",
          materials: "",
        })),
      );
      if (seed.payload.description) setDescription(seed.payload.description);
      if (seed.payload.notes) setNotes(seed.payload.notes);
      setSourceEstimate(seed.payload.sourceEstimate);
      setSeedNote(
        `Filled from estimate ${seed.estimate_reference ?? ""} (v${seed.estimate_version_number ?? "?"}) — ` +
          `subtotal ${seed.payload.subtotal?.toLocaleString("en-CA", { style: "currency", currency: "CAD" })}` +
          (seed.matches_estimate_subtotal ? "" : ", which does NOT match the estimate subtotal. Check before creating."),
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSeeding(false);
    }
  };

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const payload: InvoicePayload = { date, terms, description, notes };
      if (sourceEstimate) payload.sourceEstimate = sourceEstimate;
      if (sourceChangeOrder) payload.sourceChangeOrder = sourceChangeOrder;
      if (type === "progress") {
        payload.milestone = milestone;
        payload.lineItems = lineItems
          .filter((r) => r.description || r.amount)
          .map((r) => ({
            description: r.description,
            percentage: r.percentage ? Number(r.percentage) : undefined,
            amount: Number(r.amount) || 0,
          }));
      } else if (type === "change_order") {
        payload.changeOrderNumber = coNumber;
        payload.reason = reason;
        payload.lineItems = lineItems
          .filter((r) => r.description || r.amount)
          .map((r) => ({
            description: r.description,
            labor: r.labor ? Number(r.labor) : undefined,
            materials: r.materials ? Number(r.materials) : undefined,
            amount: Number(r.amount) || 0,
          }));
      } else if (type === "retainer") {
        payload.retainerTerms = retainerTerms;
        payload.lineItems = lineItems
          .filter((r) => r.description || r.amount)
          .map((r) => ({ description: r.description, amount: Number(r.amount) || 0 }));
      } else if (type === "tm") {
        payload.materialMarkup = materialMarkup;
        payload.labor = labor
          .filter((r) => r.worker || r.description || r.hours)
          .map((r) => ({
            worker: r.worker,
            description: r.description,
            hours: Number(r.hours) || 0,
            rate: Number(r.rate) || 0,
          }));
        payload.materials = materials
          .filter((r) => r.item)
          .map((r) => ({
            item: r.item,
            supplier: r.supplier,
            qty: Number(r.qty) || 1,
            unitCost: Number(r.unitCost) || 0,
          }));
      }
      // Every type can carry adjustments: the correction is to the account, not
      // to the kind of work being billed.
      const adj = adjustments
        .filter((r) => r.description || r.amount)
        .map((r) => ({ description: r.description, amount: Number(r.amount) || 0 }))
        .filter((r) => r.amount !== 0);
      if (adj.length) payload.adjustments = adj;

      const inv = existing
        ? await updateInvoice(existing.id, payload)
        : await createInvoice({
            project_id: projectId,
            type,
            payload,
            idempotency_key: idempotencyKey,
          });
      onCreated(inv.id);
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={busy ? undefined : onClose}
    >
      <Card
        className="max-h-[90vh] w-full max-w-3xl space-y-3 overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold" style={{ color: "var(--color-brio-accent)" }}>
          {existing ? `Edit Invoice · ${existing.number}` : "New Invoice"}
        </h2>

        <label className="block text-sm font-medium text-slate-700">
          Type
          <select
            value={type}
            onChange={(e) => {
              // Switching type invalidates anything the previous type seeded.
              // Carrying sourceEstimate onto a change-order invoice would make
              // the create refuse against a superseded estimate it never billed.
              setType(e.target.value as InvoiceType);
              setSeedNote(null);
              setSeedWarnings([]);
              setSourceEstimate(undefined);
              setSourceChangeOrder(undefined);
            }}
            disabled={busy || !!existing}
            className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            {INVOICE_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm text-slate-700">
            Date
            <Input value={date} disabled={busy} onChange={(e) => setDate(e.target.value)} />
          </label>
          <label className="block text-sm text-slate-700">
            Terms
            <Input value={terms} disabled={busy} onChange={(e) => setTerms(e.target.value)} />
          </label>
        </div>

        <label className="block text-sm text-slate-700">
          Description
          <Input
            value={description}
            disabled={busy}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>

        {type === "change_order" && (
          <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
            <div className="text-sm font-medium text-slate-700">Bill from a change order</div>
            <div className="text-sm text-slate-500">
              A change order bills work beyond the estimate, so the line items come from the
              change order&apos;s own breakdown &mdash; not from the active estimate version.
            </div>
            {cos === null ? (
              <div className="mt-2 text-sm text-slate-500">Loading change orders…</div>
            ) : cos.length === 0 ? (
              <div className="mt-2 text-sm text-slate-500">
                This project has no change orders yet. Create one on the Change Orders page
                (or generate one from the drawings there), then come back.
              </div>
            ) : (
              <div className="mt-2 flex items-end gap-3">
                <label className="flex-1 block text-sm text-slate-700">
                  Change order
                  <select
                    value={coId}
                    disabled={busy || seeding}
                    onChange={(e) => setCoId(e.target.value)}
                    className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                  >
                    {cos.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.reference} — {c.title} ({money(c.cost_impact)}, {c.status})
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  type="button"
                  variant="secondary"
                  disabled={busy || seeding || !coId}
                  onClick={seedFromChangeOrder}
                >
                  {seeding ? "Reading…" : "Fill from change order"}
                </Button>
              </div>
            )}
            {seedNote && <div className="mt-2 text-sm text-slate-700">{seedNote}</div>}
            {seedWarnings.map((w) => (
              <div
                key={w}
                className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
              >
                {w}
              </div>
            ))}
            {sourceChangeOrder?.reference && (
              <div className="mt-1 text-xs text-slate-500">
                Linked to {sourceChangeOrder.reference}.
              </div>
            )}
          </div>
        )}

        {type !== "change_order" && (
          <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm text-slate-700">
                <div className="font-medium">Bill from the active estimate</div>
                <div className="text-slate-500">
                  Fills the line items from this project&apos;s active estimate version. Change
                  orders bill work beyond the estimate and are created separately.
                </div>
              </div>
              <Button
                type="button"
                variant="secondary"
                disabled={busy || seeding}
                onClick={seedFromEstimate}
              >
                {seeding ? "Reading estimate…" : "Fill from estimate"}
              </Button>
            </div>
            {seedNote && <div className="mt-2 text-sm text-slate-700">{seedNote}</div>}
            {sourceEstimate?.reference && (
              <div className="mt-1 text-xs text-slate-500">
                Linked to estimate {sourceEstimate.reference} (v{sourceEstimate.versionNumber}).
                If that estimate is superseded before you create this invoice, the save will be
                refused so a stale price cannot be billed.
              </div>
            )}
          </div>
        )}

        {type === "progress" && (
          <label className="block text-sm text-slate-700">
            Milestone
            <Input
              value={milestone}
              disabled={busy}
              onChange={(e) => setMilestone(e.target.value)}
            />
          </label>
        )}
        {type === "change_order" && (
          <div className="grid grid-cols-2 gap-3">
            <label className="block text-sm text-slate-700">
              Change Order #
              <Input
                value={coNumber}
                disabled={busy}
                onChange={(e) => setCoNumber(e.target.value)}
              />
            </label>
            <label className="block text-sm text-slate-700">
              Reason
              <Input value={reason} disabled={busy} onChange={(e) => setReason(e.target.value)} />
            </label>
          </div>
        )}
        {type === "retainer" && (
          <label className="block text-sm text-slate-700">
            Retainer terms
            <Input
              value={retainerTerms}
              disabled={busy}
              onChange={(e) => setRetainerTerms(e.target.value)}
            />
          </label>
        )}
        {type === "tm" && (
          <label className="block text-sm text-slate-700">
            Material markup %
            <Input
              type="number"
              value={materialMarkup}
              disabled={busy}
              onChange={(e) => setMaterialMarkup(Number(e.target.value))}
            />
          </label>
        )}

        {type === "tm" ? (
          <>
            <RowEditor
              title="Labor"
              cols={[
                ["worker", "Worker / role"],
                ["description", "Description"],
                ["hours", "Hours"],
                ["rate", "Rate $/hr"],
              ]}
              rows={labor}
              onChange={setLabor}
            />
            <RowEditor
              title="Materials"
              cols={[
                ["item", "Item"],
                ["supplier", "Supplier"],
                ["qty", "Qty"],
                ["unitCost", "Unit cost"],
              ]}
              rows={materials}
              onChange={setMaterials}
            />
          </>
        ) : (
          <RowEditor
            title="Line items"
            cols={
              type === "progress"
                ? [
                    ["description", "Description"],
                    ["percentage", "% of contract"],
                    ["amount", "Amount $"],
                  ]
                : type === "change_order"
                  ? [
                      ["description", "Description"],
                      ["labor", "Labor $"],
                      ["materials", "Materials $"],
                      ["amount", "Amount $"],
                    ]
                  : [
                      ["description", "Description"],
                      ["amount", "Amount $"],
                    ]
            }
            rows={lineItems}
            onChange={setLineItems}
          />
        )}

        <RowEditor
          title="Adjustments (added after GST, not taxed)"
          cols={[
            ["description", "e.g. GST not charged on INV-1234-001"],
            ["amount", "Amount $ (negative for a credit)"],
          ]}
          rows={adjustments}
          onChange={setAdjustments}
        />

        <label className="block text-sm text-slate-700">
          Notes (appears on the invoice)
          {/* rows=2 was set when Notes held a sentence. It now carries the scope
              seeded from the estimate, which runs to a few thousand characters,
              inside a modal that already scrolls: editing meant dragging a
              two-line window through the whole document. */}
          <textarea
            rows={16}
            value={notes}
            disabled={busy}
            onChange={(e) => setNotes(e.target.value)}
            className="mt-1 w-full resize-y rounded-md border border-slate-300 px-3 py-2
                       font-mono text-xs leading-relaxed"
          />
        </label>

        {err && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {err}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy
              ? existing ? "Saving…" : "Creating…"
              : existing ? "Save Changes" : "Create Invoice"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

function RowEditor({
  title,
  cols,
  rows,
  onChange,
}: {
  title: string;
  cols: [string, string][];
  rows: Row[];
  onChange: (next: Row[]) => void;
}) {
  const blank: Row = Object.fromEntries(cols.map(([k]) => [k, ""]));
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-700">{title}</span>
        <Button variant="ghost" onClick={() => onChange([...rows, { ...blank }])}>
          + Add row
        </Button>
      </div>
      {rows.map((row, idx) => (
        <div key={idx} className="flex items-center gap-1">
          {cols.map(([k, placeholder]) => (
            <Input
              key={k}
              placeholder={placeholder}
              value={row[k] ?? ""}
              onChange={(e) => {
                const next = rows.slice();
                next[idx] = { ...next[idx], [k]: e.target.value };
                // Editing the percentage clears the amount so the server
                // re-derives it. The server treats an explicit amount as an
                // override of the percentage, which is right for a typed
                // number and wrong for one it computed itself: changing 20%
                // to 30% would otherwise keep billing the 20% figure.
                if (k === "percentage") next[idx].amount = "";
                onChange(next);
              }}
            />
          ))}
          <button
            type="button"
            className="px-1 text-sm text-slate-400 hover:text-red-600"
            onClick={() => onChange(rows.filter((_, i) => i !== idx))}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

function InvoiceDetail({
  invoiceId,
  onBack,
}: {
  invoiceId: string;
  onBack: () => void;
}) {
  const [invoice, setInvoice] = useState<InvoiceRead | null>(null);
  const [payments, setPayments] = useState<PaymentRead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [payAmount, setPayAmount] = useState("");
  const [payMethod, setPayMethod] = useState("E-Transfer");
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    try {
      const [inv, pays] = await Promise.all([
        getInvoice(invoiceId),
        listPayments(invoiceId),
      ]);
      setInvoice(inv);
      setPayments(pays);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [invoiceId]);

  useEffect(() => {
    load();
  }, [load]);

  const doRecordPayment = async () => {
    if (!payAmount) return;
    setBusy("payment");
    setError(null);
    try {
      await recordPayment(invoiceId, {
        amount: Number(payAmount),
        method: payMethod,
      });
      setPayAmount("");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const doPdf = async () => {
    setBusy("pdf");
    setError(null);
    try {
      const r = await generateInvoicePdf(invoiceId);
      window.open(r.download_url, "_blank", "noopener");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const doDelete = async () => {
    // Draft-only by policy: once issued, an invoice keeps its number and cannot
    // be removed. The API enforces this too; this is only the prompt.
    if (!confirm(`Delete draft ${invoice?.number ?? "invoice"}? This cannot be undone.`)) return;
    setBusy("delete");
    setError(null);
    try {
      await deleteInvoice(invoiceId);
      onBack();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const copyLink = async () => {
    setBusy("link");
    setError(null);
    try {
      const { token } = await getShareToken(invoiceId);
      const url = `${window.location.origin}/invoice/${token}`;
      await navigator.clipboard.writeText(url);
      alert(`Client link copied to clipboard:\n${url}`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const changeStatus = async (next: InvoiceStatus) => {
    if (!invoice || next === invoice.status) return;
    if (
      next === "paid" &&
      !confirm("Mark this invoice paid without recording a payment against it?")
    )
      return;
    setBusy("status");
    setError(null);
    try {
      await updateInvoice(invoiceId, undefined, next);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const doSend = async () => {
    // Name the amount and the consequence. The old prompt asked only whether to
    // send, so the two things worth pausing over were invisible: it goes to a
    // client, and a sent invoice can no longer be deleted.
    if (!invoice) return;
    const amount = money(p.total ?? 0);
    if (
      !confirm(
        `Email ${invoice.number} (${amount}) to the client with the PDF attached?\n\n` +
          `The invoice will be marked Sent and can no longer be deleted.`,
      )
    )
      return;
    setBusy("send");
    setError(null);
    try {
      const r = await sendInvoice(invoiceId);
      alert(`Invoice emailed to ${r.sent_to}.`);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  if (error && !invoice) {
    return (
      <div className="space-y-4">
        <button onClick={onBack} className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to invoices
        </button>
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      </div>
    );
  }
  if (!invoice) return <div className="text-slate-400">Loading invoice…</div>;

  const p = invoice.payload;
  const total = Number(p.total ?? 0);
  const paid = Number(invoice.paid_amount);
  const balance = total - paid;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to invoices
        </button>
        <div className="flex gap-2">
          {invoice.status === "draft" && (
            <Button variant="secondary" disabled={!!busy} onClick={() => setEditing(true)}>
              Edit
            </Button>
          )}
          {invoice.status === "draft" && (
            <Button variant="secondary" disabled={!!busy} onClick={doDelete}>
              {busy === "delete" ? "…" : "Delete"}
            </Button>
          )}
          <Button variant="secondary" disabled={!!busy} onClick={copyLink}>
            {busy === "link" ? "…" : "Copy Client Link"}
          </Button>
          <Button variant="secondary" disabled={!!busy} onClick={doPdf}>
            {busy === "pdf" ? "…" : "Generate PDF"}
          </Button>
          <Button disabled={!!busy} onClick={doSend}>
            {busy === "send" ? "Sending…" : "Send to Client"}
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <Card className="p-4 space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold" style={{ color: "var(--color-brio-accent)" }}>
            {invoice.number}
          </h2>
          <StatusBadge status={invoice.status} />
          {/* Until now status could only change as a side effect of sending or
              of recording a payment. There was no way to void an invoice or to
              correct a status by hand without going at the API directly. */}
          <select
            aria-label="Invoice status"
            className="ml-auto rounded border border-slate-300 bg-white px-2 py-1 text-xs"
            value={invoice.status}
            disabled={!!busy}
            onChange={(e) => changeStatus(e.target.value as InvoiceStatus)}
          >
            {(["draft", "sent", "partial", "paid", "overdue", "void"] as const).map((st) => (
              <option key={st} value={st}>
                {st}
              </option>
            ))}
          </select>
        </div>
        <div className="text-sm text-slate-500">
          {invoice.type} · {p.date ?? "—"} · terms {p.terms ?? "—"}
        </div>
        {p.description && <p className="text-sm text-slate-700">{p.description}</p>}

        {/* The scope seeded from the estimate. It goes on the PDF and on the
            client's copy, so leaving it off this screen meant the only way to
            see what the client would actually receive was to generate the PDF
            and open it. whitespace-pre-wrap because the scope is seeded with
            real line breaks and indentation. */}
        {p.notes && (
          <details className="rounded border border-slate-200 bg-slate-50 px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium text-slate-700">
              Scope and notes on this invoice
            </summary>
            <p className="mt-2 whitespace-pre-wrap text-sm text-slate-600">{p.notes}</p>
          </details>
        )}
        <table className="ml-auto w-full max-w-sm text-sm">
          <tbody>
            <tr>
              <td className="py-1 text-slate-600">Subtotal</td>
              <td className="py-1 text-right">{money(p.subtotal ?? 0)}</td>
            </tr>
            <tr>
              <td className="py-1 text-slate-600">GST (5%)</td>
              <td className="py-1 text-right">{money(p.gst ?? 0)}</td>
            </tr>
            {(p.adjustments ?? []).map((a, i) => (
              <tr key={i}>
                <td className="py-1 text-slate-600">{a.description || "Adjustment"}</td>
                <td className="py-1 text-right">{money(a.amount ?? 0)}</td>
              </tr>
            ))}
            <tr className="border-t font-semibold">
              <td className="py-1">Total</td>
              <td className="py-1 text-right">{money(total)}</td>
            </tr>
            <tr>
              <td className="py-1 text-emerald-600">Paid</td>
              <td className="py-1 text-right text-emerald-600">{money(paid)}</td>
            </tr>
            <tr
              className="border-t-2 font-semibold"
              style={{ borderColor: "var(--color-brio-primary)" }}
            >
              <td className="py-1">Balance Due</td>
              <td className="py-1 text-right">{money(balance)}</td>
            </tr>
          </tbody>
        </table>
      </Card>

      <Card className="p-4 space-y-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Payments ({payments.length})
        </h3>
        {payments.length === 0 ? (
          <div className="text-xs text-slate-400">No payments recorded.</div>
        ) : (
          <ul className="space-y-1 text-sm">
            {payments.map((pay) => (
              <li
                key={pay.id}
                className="flex justify-between border-b border-slate-100 py-1"
              >
                <span className="text-slate-600">
                  {new Date(pay.paid_at).toLocaleDateString()} · {pay.method}
                </span>
                <span className="text-slate-800">{money(pay.amount)}</span>
              </li>
            ))}
          </ul>
        )}
        {invoice.status !== "paid" && (
          <div className="flex items-end gap-2 border-t border-slate-100 pt-3">
            <label className="text-sm text-slate-600">
              Amount
              <Input
                type="number"
                value={payAmount}
                onChange={(e) => setPayAmount(e.target.value)}
                className="w-32"
              />
            </label>
            <label className="text-sm text-slate-600">
              Method
              <select
                value={payMethod}
                onChange={(e) => setPayMethod(e.target.value)}
                className="block rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              >
                <option>E-Transfer</option>
                <option>Cheque</option>
                <option>Cash</option>
                <option>Credit Card</option>
              </select>
            </label>
            <Button disabled={busy === "payment" || !payAmount} onClick={doRecordPayment}>
              {busy === "payment" ? "…" : "Record Payment"}
            </Button>
          </div>
        )}
      </Card>

      {editing && (
        <InvoiceModal
          projectId={invoice.project_id}
          existing={invoice}
          onClose={() => setEditing(false)}
          onCreated={() => {
            setEditing(false);
            load();
          }}
        />
      )}
    </div>
  );
}
