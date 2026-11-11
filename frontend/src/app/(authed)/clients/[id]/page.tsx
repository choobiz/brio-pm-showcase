"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import { Card } from "@/components/ui/card";
import { formatMoney, getClient, type ClientDetail } from "@/lib/clients";

export default function ClientDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [client, setClient] = useState<ClientDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getClient(id)
      .then(setClient)
      .catch((e) => setError(String(e)));
  }, [id]);

  if (error) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        {error}
      </div>
    );
  }
  if (!client) {
    return <div className="text-slate-400">Loading client...</div>;
  }

  return (
    <div className="space-y-6">
      <div>
        <Link href="/clients" className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to clients
        </Link>
        <h1 className="mt-2 text-2xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>
          {client.name}
        </h1>
        {client.tags.length > 0 && (
          <div className="mt-1 flex gap-2">
            {client.tags.map((t) => (
              <span
                key={t}
                className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700"
              >
                {t}
              </span>
            ))}
          </div>
        )}
      </div>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="p-4">
          <div className="text-xs uppercase tracking-wide text-slate-500">Total billed</div>
          <div className="mt-1 text-xl font-semibold">{formatMoney(client.total_billed)}</div>
        </Card>
        <Card className="p-4">
          <div className="text-xs uppercase tracking-wide text-slate-500">Total paid</div>
          <div className="mt-1 text-xl font-semibold">{formatMoney(client.total_paid)}</div>
        </Card>
        <Card className="p-4">
          <div className="text-xs uppercase tracking-wide text-slate-500">Outstanding</div>
          <div className="mt-1 text-xl font-semibold">{formatMoney(client.total_outstanding)}</div>
        </Card>
      </section>

      <Card className="p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Contact
        </h2>
        <dl className="mt-2 grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-2 text-sm">
          <div>
            <dt className="text-slate-500">Emails</dt>
            <dd className="text-slate-800">
              {client.emails.length ? client.emails.join(", ") : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">Phones</dt>
            <dd className="text-slate-800">
              {client.phones.length ? client.phones.join(", ") : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">Addresses</dt>
            <dd className="text-slate-800">
              {client.addresses.length ? client.addresses.join("; ") : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">First seen</dt>
            <dd className="text-slate-800">{client.first_seen ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Last seen</dt>
            <dd className="text-slate-800">{client.last_seen ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Last activity</dt>
            <dd className="text-slate-800">{client.last_activity ?? "—"}</dd>
          </div>
        </dl>
        {client.notes && (
          <div className="mt-3 rounded-md bg-slate-50 p-3 text-sm text-slate-700 whitespace-pre-wrap">
            {client.notes}
          </div>
        )}
      </Card>

      <Card className="p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Projects ({client.projects.length})
        </h2>
        {client.projects.length === 0 ? (
          <div className="mt-3 text-sm text-slate-400">No projects.</div>
        ) : (
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-2">Name</th>
                <th className="py-2">Address</th>
                <th className="py-2">Status</th>
                <th className="py-2 text-right">Contract</th>
                <th className="py-2 text-right">Billed</th>
                <th className="py-2 text-right">Paid</th>
              </tr>
            </thead>
            <tbody>
              {client.projects.map((p) => (
                <tr key={p.id} className="border-t border-slate-100">
                  <td className="py-2">{p.name}</td>
                  <td className="py-2 text-slate-600">{p.address ?? "—"}</td>
                  <td className="py-2 text-slate-600">{p.status}</td>
                  <td className="py-2 text-right">{formatMoney(p.contract_value)}</td>
                  <td className="py-2 text-right">{formatMoney(p.total_billed)}</td>
                  <td className="py-2 text-right">{formatMoney(p.total_paid)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card className="p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Invoices ({client.invoices.length})
        </h2>
        {client.invoices.length === 0 ? (
          <div className="mt-3 text-sm text-slate-400">No invoices.</div>
        ) : (
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-2">Number</th>
                <th className="py-2">Type</th>
                <th className="py-2">Status</th>
                <th className="py-2 text-right">Total</th>
                <th className="py-2 text-right">Paid</th>
              </tr>
            </thead>
            <tbody>
              {client.invoices.map((inv) => (
                <tr key={inv.id} className="border-t border-slate-100">
                  <td className="py-2 font-mono text-xs">{inv.number}</td>
                  <td className="py-2 text-slate-600">{inv.type}</td>
                  <td className="py-2 text-slate-600">{inv.status}</td>
                  <td className="py-2 text-right">{formatMoney(inv.total)}</td>
                  <td className="py-2 text-right">{formatMoney(inv.paid_amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card className="p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Payments ({client.payments.length})
        </h2>
        {client.payments.length === 0 ? (
          <div className="mt-3 text-sm text-slate-400">No payments yet.</div>
        ) : (
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-2">Date</th>
                <th className="py-2">Method</th>
                <th className="py-2 text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {client.payments.map((p) => (
                <tr key={p.id} className="border-t border-slate-100">
                  <td className="py-2 text-slate-600">{p.paid_at ?? "—"}</td>
                  <td className="py-2 text-slate-600">{p.method}</td>
                  <td className="py-2 text-right">{formatMoney(p.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card className="p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Documents ({client.documents.length})
        </h2>
        {client.documents.length === 0 ? (
          <div className="mt-3 text-sm text-slate-400">No documents.</div>
        ) : (
          <ul className="mt-2 text-sm text-slate-700 space-y-1">
            {client.documents.map((d) => (
              <li key={d.id} className="flex justify-between border-t border-slate-100 py-1">
                <span>{d.filename}</span>
                <span className="text-slate-500">{d.category}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
