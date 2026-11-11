"use client";

import { use, useEffect, useState } from "react";

import { COMPANY } from "@/lib/company";

interface PublicInvoice {
  number: string;
  type: string;
  status: string;
  project_name: string | null;
  date: string | null;
  terms: string | null;
  description: string | null;
  milestone: string | null;
  line_items: { description?: string; percentage?: number; amount?: number }[];
  labor: { worker?: string; description?: string; hours?: number; rate?: number }[];
  materials: { item?: string; supplier?: string; qty?: number; unitCost?: number }[];
  subtotal: number | null;
  gst: number | null;
  total: number;
  paid: number;
  balance_due: number;
  notes: string | null;
  pdf_url: string | null;
}

function money(n: number | null | undefined): string {
  return Number(n ?? 0).toLocaleString("en-CA", {
    style: "currency",
    currency: "CAD",
  });
}

export default function PublicInvoicePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const [invoice, setInvoice] = useState<PublicInvoice | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/backend/public/invoice/${encodeURIComponent(token)}`, {
      cache: "no-store",
    })
      .then((r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "This invoice link is not valid." : `Error ${r.status}`);
        return r.json();
      })
      .then(setInvoice)
      .catch((e) => setError(String(e.message ?? e)));
  }, [token]);

  if (error) {
    return (
      <main className="mx-auto max-w-md px-4 py-20 text-center">
        <div className="text-lg font-semibold" style={{ color: "#0068A0" }}>
          BUILT BY BRIO
        </div>
        <p className="mt-6 text-sm text-slate-500">{error}</p>
      </main>
    );
  }
  if (!invoice) {
    return (
      <main className="mx-auto max-w-md px-4 py-20 text-center text-slate-400">
        Loading invoice…
      </main>
    );
  }

  const lineItems = invoice.line_items.filter((i) => i.description || i.amount);
  const isTM = invoice.labor.length > 0 || invoice.materials.length > 0;

  return (
    <main className="mx-auto max-w-2xl px-4 py-10">
      <div className="border border-slate-200 bg-white">
        {/* Header */}
        <div
          className="flex items-center justify-between border-b-2 px-6 py-5"
          style={{ borderColor: "#0068A0" }}
        >
          <div className="text-xl font-bold tracking-wide" style={{ color: "#0068A0" }}>
            BUILT BY <span style={{ color: "#1D3146" }}>BRIO</span>
          </div>
          <div className="text-right text-xs text-slate-500">
            {COMPANY.legalName}<br />
            {COMPANY.email} · {COMPANY.phone}
          </div>
        </div>

        <div className="space-y-5 px-6 py-6">
          <div>
            <h1 className="text-2xl font-bold" style={{ color: "#0068A0" }}>
              Invoice {invoice.number}
            </h1>
            <p className="text-sm text-slate-500">
              {invoice.project_name ?? "Project"} · {invoice.date ?? "—"} ·{" "}
              <span className="uppercase">{invoice.status}</span>
            </p>
          </div>

          {invoice.description && (
            <p className="text-sm text-slate-700">{invoice.description}</p>
          )}

          {/* Line items */}
          {lineItems.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b-2 text-left text-xs uppercase text-slate-500" style={{ borderColor: "#0068A0" }}>
                  <th className="py-2">Description</th>
                  <th className="py-2 text-right">Amount</th>
                </tr>
              </thead>
              <tbody>
                {lineItems.map((it, i) => (
                  <tr key={i} className="border-b border-slate-100">
                    <td className="py-2 text-slate-700">{it.description}</td>
                    <td className="py-2 text-right">{money(it.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {isTM && (
            <p className="text-sm text-slate-600">
              Time &amp; materials — {invoice.labor.length} labor and{" "}
              {invoice.materials.length} material line(s). See the attached PDF
              for the full breakdown.
            </p>
          )}

          {/* Totals */}
          <table className="ml-auto w-full max-w-xs text-sm">
            <tbody>
              <tr>
                <td className="py-1 text-slate-600">Subtotal</td>
                <td className="py-1 text-right">{money(invoice.subtotal)}</td>
              </tr>
              <tr>
                <td className="py-1 text-slate-600">GST (5%)</td>
                <td className="py-1 text-right">{money(invoice.gst)}</td>
              </tr>
              <tr className="border-t font-semibold">
                <td className="py-1">Total</td>
                <td className="py-1 text-right">{money(invoice.total)}</td>
              </tr>
              {invoice.paid > 0 && (
                <tr>
                  <td className="py-1 text-emerald-600">Paid</td>
                  <td className="py-1 text-right text-emerald-600">
                    {money(invoice.paid)}
                  </td>
                </tr>
              )}
              <tr className="border-t-2 font-bold" style={{ borderColor: "#0068A0" }}>
                <td className="py-1">Balance Due</td>
                <td className="py-1 text-right">{money(invoice.balance_due)}</td>
              </tr>
            </tbody>
          </table>

          {/* Payment instructions */}
          <div
            className="border-l-4 px-4 py-3 text-sm"
            style={{ borderColor: "#0068A0", background: "#F0F7FC" }}
          >
            <div className="font-semibold text-slate-700">Payment</div>
            <div className="text-slate-600">
              E-Transfer: amir@builtbybrio.com
              <br />
              Cheque payable to: Pacific Prime Projects Inc.
              {invoice.terms && (
                <>
                  <br />
                  Terms: {invoice.terms}
                </>
              )}
            </div>
          </div>

          {invoice.notes && (
            <p className="whitespace-pre-wrap text-sm text-slate-500">{invoice.notes}</p>
          )}

          {invoice.pdf_url && (
            <a
              href={invoice.pdf_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block px-4 py-2 text-sm font-medium text-white"
              style={{ background: "#0068A0" }}
            >
              Download PDF
            </a>
          )}
        </div>
      </div>
      <p className="mt-4 text-center text-xs text-slate-400">
        Thank you for trusting BRIO — builtbybrio.com
      </p>
    </main>
  );
}
