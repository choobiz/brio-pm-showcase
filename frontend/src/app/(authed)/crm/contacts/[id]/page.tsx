"use client";

/** The Firm — everything known about one prospect, enough to walk into a call. */

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { CrmDraft } from "@/components/CrmDraft";
import {
  getCrmContact, money, promoteContact,
  type CrmContactDetail,
} from "@/lib/crm";

export default function CrmContactPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [c, setC] = useState<CrmContactDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getCrmContact(id).then(setC).catch((e) => setError(String(e)));
  }, [id]);
  useEffect(load, [load]);

  if (error) return <p className="text-sm text-rose-700">{error}</p>;
  if (!c) return <p className="text-sm text-slate-500">Loading…</p>;

  const hasOpen = c.opportunities.some(
    (o) => !["won", "lost", "dormant"].includes(o.status));

  async function promote() {
    setBusy(true);
    try { await promoteContact(id); load(); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  return (
    <div className="max-w-4xl">
      <Link href="/crm" className="text-sm text-slate-500 hover:underline">
        ← This week
      </Link>

      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{c.name}</h1>
          <p className="mt-0.5 text-sm text-slate-600">
            Tier {c.tier ?? "–"} · {c.segment ?? "unclassified"}
            {c.cities && ` · ${c.cities.replace(/\|/g, ", ")}`}
          </p>
          {c.permit_name && c.permit_name !== c.name && (
            <p className="mt-0.5 text-xs text-slate-400">
              Filed on permits as “{c.permit_name}”
            </p>
          )}
        </div>
        {!hasOpen && !c.excluded_reason && (
          <Button onClick={promote} disabled={busy}>
            {busy ? "Adding…" : "Add to this week"}
          </Button>
        )}
      </div>

      {c.excluded_reason && (
        <p className="mt-3 rounded border border-rose-200 bg-rose-50 p-2 text-sm text-rose-900">
          Excluded — {c.excluded_reason}. This firm cannot be added to the queue.
        </p>
      )}

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Panel title="Permit history">
          <Row k="Permits" v={String(c.permits_total ?? "—")} />
          <Row k="As applicant" v={String(c.permits_as_applicant ?? "—")} />
          <Row k="Project value" v={money(c.value_as_applicant)} />
          <Row k="Typical work" v={c.top_use_category ?? "—"} />
        </Panel>

        <Panel title="Contact">
          <Row k="Email" v={c.email ?? "—"} />
          <Row k="Phone" v={c.phone ?? "—"} />
          <Row k="Website" v={c.website ?? "—"} />
          <Row k="Confidence" v={c.contact_confidence ?? "—"}
               warn={c.contact_confidence === "medium"} />
        </Panel>

        <Panel title="BC registry">
          <Row k="Registration" v={c.bc_registration ?? "not registered"} />
          <Row k="Legal name" v={c.legal_name ?? "—"} />
          <Row k="Type" v={c.entity_type === "BC" ? "Incorporated (BC)"
                            : c.entity_type ? `Sole proprietor / firm (${c.entity_type})`
                            : "—"} />
          <Row k="Status" v={c.registry_status ?? "—"} />
        </Panel>

        <Panel title="Pipeline">
          {c.opportunities.length === 0 && (
            <p className="text-sm text-slate-500">Not in the queue.</p>
          )}
          {c.opportunities.map((o) => (
            <div key={o.id} className="border-b border-slate-100 py-1.5 last:border-0">
              <div className="text-sm">
                <span className="font-medium">{o.track}</span> · {o.status}
              </div>
              <div className="text-xs text-slate-500">
                {o.next_action ?? "no next action"}
                {o.next_action_at && ` · due ${o.next_action_at}`}
              </div>
            </div>
          ))}
        </Panel>
      </div>

      {!c.excluded_reason && <CrmDraft contact={c} />}

      <h2 className="mt-6 mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
        History
      </h2>
      {c.interactions.length === 0 ? (
        <p className="text-sm text-slate-500">Nothing logged yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {c.interactions.map((i) => (
            <li key={i.id} className="rounded border border-slate-200 bg-white p-2.5">
              <div className="flex flex-wrap items-baseline gap-2 text-xs text-slate-500">
                <span className="font-medium text-slate-700">{i.channel}</span>
                <span>{i.direction}</span>
                <span>{new Date(i.occurred_at).toLocaleString()}</span>
              </div>
              {i.summary && <p className="mt-0.5 text-sm">{i.summary}</p>}
              {i.outcome && (
                <p className="mt-0.5 text-sm text-slate-600">→ {i.outcome}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded border border-slate-200 bg-white p-3">
      <h2 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Row({ k, v, warn }: { k: string; v: string; warn?: boolean }) {
  return (
    <div className="flex justify-between gap-3 py-0.5 text-sm">
      <span className="text-slate-500">{k}</span>
      <span className={`truncate text-right ${warn ? "text-amber-800" : ""}`}>{v}</span>
    </div>
  );
}
