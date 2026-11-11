"use client";

/**
 * Card leads — people who sent their details from a BRIO business card
 * (builtbybrio.com/card/<slug>). The website forwards each submission to
 * POST /public/card-leads; this screen is where they get triaged.
 *
 * Opens on "New" because that is the follow-up list. Contacted and archived
 * rows stay one click away rather than cluttering it.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  listCardLeads, setCardLeadStatus,
  type CardLead, type CardLeadStatus,
} from "@/lib/crm";

const FILTERS: { value: CardLeadStatus | ""; label: string }[] = [
  { value: "new", label: "New" },
  { value: "contacted", label: "Contacted" },
  { value: "archived", label: "Archived" },
  { value: "", label: "All" },
];

const STATUS_CLS: Record<CardLeadStatus, string> = {
  new: "bg-amber-100 text-amber-900 border-amber-200",
  contacted: "bg-emerald-100 text-emerald-900 border-emerald-200",
  archived: "bg-slate-100 text-slate-600 border-slate-200",
};

function when(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function StatusBadge({ status }: { status: CardLeadStatus }) {
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 text-xs ${STATUS_CLS[status] ?? STATUS_CLS.archived}`}>
      {status}
    </span>
  );
}

function Actions({ lead, busy, onSet }: {
  lead: CardLead; busy: boolean; onSet: (s: CardLeadStatus) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {lead.status !== "contacted" && (
        <Button className="px-2 py-1 text-xs" disabled={busy} onClick={() => onSet("contacted")}>
          Mark contacted
        </Button>
      )}
      {lead.status !== "archived" && (
        <Button variant="secondary" className="px-2 py-1 text-xs" disabled={busy}
                onClick={() => onSet("archived")}>
          Archive
        </Button>
      )}
      {lead.status === "archived" && (
        <Button variant="ghost" className="px-2 py-1 text-xs" disabled={busy}
                onClick={() => onSet("new")}>
          Restore
        </Button>
      )}
    </div>
  );
}

function Contact({ lead }: { lead: CardLead }) {
  return (
    <>
      {lead.phone && (
        <a href={`tel:${lead.phone.replace(/[^\d+]/g, "")}`} className="block text-sky-800 hover:underline">
          {lead.phone}
        </a>
      )}
      {lead.email && (
        <a href={`mailto:${lead.email}`} className="block break-all text-sky-800 hover:underline">
          {lead.email}
        </a>
      )}
    </>
  );
}

export default function CardLeadsPage() {
  const [rows, setRows] = useState<CardLead[]>([]);
  const [filter, setFilter] = useState<CardLeadStatus | "">("new");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    listCardLeads(filter || undefined)
      .then((r) => { setRows(r); setError(null); })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const update = async (id: string, status: CardLeadStatus) => {
    setBusyId(id);
    try {
      await setCardLeadStatus(id, status);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="max-w-6xl">
      <h1 className="mb-1 text-2xl font-semibold tracking-tight">Card leads</h1>
      <p className="mb-4 text-sm text-slate-600">
        People who sent their details from a BRIO business card.{" "}
        <Link href="/crm" className="underline">This week</Link> ·{" "}
        <Link href="/crm/contacts" className="underline">Prospects</Link>
      </p>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.label}
            onClick={() => setFilter(f.value)}
            className={`rounded border px-2.5 py-1 text-sm ${
              filter === f.value
                ? "border-slate-800 bg-slate-800 text-white"
                : "border-slate-300 text-slate-600 hover:bg-slate-50"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {loading && <p className="text-sm text-slate-500">Loading…</p>}
      {error && <p className="text-sm text-rose-700">{error}</p>}
      {!loading && !error && rows.length === 0 && (
        <div className="rounded border border-slate-200 bg-white p-8 text-center text-sm text-slate-600">
          No {filter || ""} card leads.
        </div>
      )}

      {rows.length > 0 && (
        <>
          {/* Phone: one card per lead. */}
          <ul className="space-y-2 md:hidden">
            {rows.map((l) => (
              <li key={l.id} className="rounded border border-slate-200 bg-white p-3 text-sm">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-medium">{l.name}</div>
                    <div className="text-xs text-slate-500">
                      {[l.company, l.role].filter(Boolean).join(" · ") || "—"}
                    </div>
                  </div>
                  <StatusBadge status={l.status} />
                </div>
                <div className="mt-2"><Contact lead={l} /></div>
                {l.note && <p className="mt-2 whitespace-pre-wrap text-slate-700">{l.note}</p>}
                <div className="mt-2 text-xs text-slate-500">
                  {when(l.created_at)} · card: {l.card_slug}{l.source ? ` (${l.source})` : ""}
                </div>
                <div className="mt-2">
                  <Actions lead={l} busy={busyId === l.id} onSet={(s) => update(l.id, s)} />
                </div>
              </li>
            ))}
          </ul>

          {/* Desktop: table. */}
          <div className="hidden overflow-x-auto rounded border border-slate-200 bg-white md:block">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="p-2 font-medium">Date</th>
                  <th className="p-2 font-medium">Name</th>
                  <th className="p-2 font-medium">Company</th>
                  <th className="p-2 font-medium">Role</th>
                  <th className="p-2 font-medium">Phone / email</th>
                  <th className="p-2 font-medium">Note</th>
                  <th className="p-2 font-medium">Card</th>
                  <th className="p-2 font-medium">Status</th>
                  <th className="p-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((l) => (
                  <tr key={l.id} className="border-b border-slate-100 align-top last:border-0 hover:bg-slate-50">
                    <td className="whitespace-nowrap p-2 text-xs text-slate-500">{when(l.created_at)}</td>
                    <td className="p-2 font-medium">{l.name}</td>
                    <td className="p-2">{l.company ?? "—"}</td>
                    <td className="p-2 text-xs">{l.role ?? "—"}</td>
                    <td className="p-2 text-xs"><Contact lead={l} /></td>
                    <td className="max-w-xs p-2 text-xs whitespace-pre-wrap text-slate-700">{l.note ?? "—"}</td>
                    <td className="p-2 text-xs text-slate-500">
                      {l.card_slug}{l.source ? ` (${l.source})` : ""}
                    </td>
                    <td className="p-2"><StatusBadge status={l.status} /></td>
                    <td className="p-2">
                      <Actions lead={l} busy={busyId === l.id} onSet={(s) => update(l.id, s)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
