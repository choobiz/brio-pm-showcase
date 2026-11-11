"use client";

/**
 * The List — exists so you can find a firm and pull it into the queue.
 * Deliberately NOT the landing screen: 4,916 rows is a filing cabinet.
 * Opens filtered to Tier A/B, which is the set actually worth working.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import { listCrmContacts, money, type CrmContact } from "@/lib/crm";

const SEGMENTS = ["", "design", "gc", "design_build", "institutional", "developer"];

export default function CrmContactsPage() {
  const [rows, setRows] = useState<CrmContact[]>([]);
  const [q, setQ] = useState("");
  const [tier, setTier] = useState("AB");
  const [segment, setSegment] = useState("");
  const [withContact, setWithContact] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    listCrmContacts({
      q: q || undefined,
      tier: tier || undefined,
      segment: segment || undefined,
      hasContact: withContact ? true : undefined,
      limit: 200,
    })
      .then((r) => { setRows(r); setError(null); })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [q, tier, segment, withContact]);

  useEffect(() => {
    const t = setTimeout(load, 250);   // debounce the search box
    return () => clearTimeout(t);
  }, [load]);

  return (
    <div className="max-w-5xl">
      <h1 className="mb-1 text-2xl font-semibold tracking-tight">Prospects</h1>
      <p className="mb-4 text-sm text-slate-600">
        Firms derived from municipal building permits.{" "}
        <Link href="/crm" className="underline">This week</Link> is where the work happens.{" "}
        People from the business card are under{" "}
        <Link href="/crm/card-leads" className="underline">Card leads</Link>.
      </p>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input className="w-56" placeholder="Search firms…" value={q}
               onChange={(e) => setQ(e.target.value)} />
        <select className="rounded border border-slate-300 px-2 py-1.5 text-sm"
                value={tier} onChange={(e) => setTier(e.target.value)}>
          <option value="AB">Tier A + B</option>
          <option value="A">Tier A</option>
          <option value="B">Tier B</option>
          <option value="C">Tier C</option>
          <option value="D">Tier D</option>
          <option value="">All tiers</option>
        </select>
        <select className="rounded border border-slate-300 px-2 py-1.5 text-sm"
                value={segment} onChange={(e) => setSegment(e.target.value)}>
          {SEGMENTS.map((s) =>
            <option key={s} value={s}>{s || "All segments"}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-sm text-slate-600">
          <input type="checkbox" checked={withContact}
                 onChange={(e) => setWithContact(e.target.checked)} />
          Has contact details
        </label>
      </div>

      {loading && <p className="text-sm text-slate-500">Loading…</p>}
      {error && <p className="text-sm text-rose-700">{error}</p>}
      {!loading && !error && (
        <p className="mb-2 text-xs text-slate-500">{rows.length} firms</p>
      )}

      <div className="overflow-x-auto rounded border border-slate-200 bg-white">
        <table className="w-full min-w-[46rem] text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
              <th className="p-2 font-medium">Tier</th>
              <th className="p-2 font-medium">Firm</th>
              <th className="p-2 text-right font-medium">Permits</th>
              <th className="p-2 text-right font-medium">Value</th>
              <th className="p-2 font-medium">Contact</th>
              <th className="p-2 font-medium">Registry</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <td className="p-2 font-mono text-xs">{c.tier ?? "–"}</td>
                <td className="p-2">
                  <Link href={`/crm/contacts/${c.id}`} className="hover:underline">
                    {c.name}
                  </Link>
                  <div className="text-xs text-slate-500">{c.segment}</div>
                </td>
                <td className="p-2 text-right tabular-nums">{c.permits_total ?? "—"}</td>
                <td className="p-2 text-right tabular-nums">{money(c.value_as_applicant)}</td>
                <td className="p-2">
                  <div className="truncate text-xs">{c.email ?? c.phone ?? "—"}</div>
                  {c.contact_confidence === "medium" && (
                    <span className="text-[11px] text-amber-800">unverified</span>
                  )}
                </td>
                <td className="p-2 text-xs text-slate-500">
                  {c.bc_registration ?? "not registered"}
                  {c.entity_type && ` · ${c.entity_type}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
