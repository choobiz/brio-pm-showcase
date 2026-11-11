"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { errorMessage, workspaceGet } from "@/lib/workspaceApi";

/** Libraries (round 2 board R2Libraries): what every estimate prices from,
 *  what needs attention there, and a card per library. */

type Overview = {
  needs: { kind: string; count: number; text: string; items: string[]; href: string }[];
  cards: {
    rates: { assemblies: number; materials: number; labour: number };
    catalogue: { supplier: string; lines: number }[];
    trades: number;
    divisions: string[];
    cost_trends: { points: number; divisions: number; since: string | null; latest: string | null };
    actual_costs: { bills: number; projects: number };
  };
};

const quarter = (iso: string | null) => {
  if (!iso) return "—";
  const [y, m] = iso.split("-").map(Number);
  return `Q${Math.floor((m - 1) / 3) + 1} ${y}`;
};

export default function LibrariesPage() {
  const [o, setO] = useState<Overview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { workspaceGet<Overview>("/libraries/overview").then(setO, (e) => setErr(errorMessage(e))); }, []);

  const card = "flex min-w-0 flex-[1_1_280px] flex-col gap-1.5 rounded border border-slate-200 bg-white p-4 hover:border-slate-400";
  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <header>
        <h1 className="text-2xl font-semibold">Libraries</h1>
        <p className="mt-1 text-sm text-slate-600">What every estimate prices from.</p>
      </header>
      {err && <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{err}</div>}
      {!o && !err && <p className="text-sm text-slate-500">Loading…</p>}
      {o && (
        <>
          <section className="rounded border border-slate-200 bg-white px-4 py-3">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-600">Needs you · {o.needs.length}</h2>
            {o.needs.length === 0 ? <p className="mt-2 text-sm text-slate-600">Nothing waiting.</p> : (
              <ul className="mt-1 divide-y divide-slate-100">
                {o.needs.map((n) => (
                  <li key={n.kind} className="py-2.5 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="min-w-0 flex-1"><b>{n.text.split(":")[0]}</b>{n.text.includes(":") ? `:${n.text.split(":").slice(1).join(":")}` : ""}</span>
                      {n.items.length > 0 && (
                        <button type="button" className="text-xs underline" onClick={() => setOpen(open === n.kind ? null : n.kind)}>
                          {open === n.kind ? "Hide" : "Which ones"}
                        </button>
                      )}
                      <Link href={n.href} className="rounded border border-slate-900 px-2.5 py-1 text-xs">Review</Link>
                    </div>
                    {open === n.kind && <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">{n.items.map((i) => <li key={i}>{i}</li>)}</ul>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <div className="flex flex-wrap gap-4">
            <Link href="/rates" className={card}>
              <h3 className="text-base font-semibold">Rates</h3>
              <span className="text-sm">{o.cards.rates.assemblies} active assemblies · {o.cards.rates.materials} materials · {o.cards.rates.labour} labour rates</span>
              <span className="text-xs text-slate-600">Linked to estimate lines.</span>
            </Link>
            <Link href="/rates?tab=catalogue" className={card}>
              <h3 className="text-base font-semibold">Supplier catalogue</h3>
              <span className="text-sm">{o.cards.catalogue.length ? o.cards.catalogue.map((c) => `${c.supplier} · ${c.lines.toLocaleString("en-CA")} lines`).join("; ") : "No price sheet imported"}</span>
              <span className="text-xs text-slate-600">Import a price sheet; add a line to Rates when you use it.</span>
            </Link>
            <Link href="/trades" className={card}>
              <h3 className="text-base font-semibold">Trades</h3>
              <span className="text-sm">{o.cards.trades} trade contacts</span>
              <span className="text-xs text-slate-600">Who we call for each trade, with their rates.</span>
            </Link>
            <Link href="/divisions" className={card}>
              <h3 className="text-base font-semibold">Cost divisions</h3>
              <span className="text-sm">{o.cards.divisions.join(" · ") || "—"}</span>
              <span className="text-xs text-slate-600">Picked by project type.</span>
            </Link>
            <div className={card}>
              <h3 className="text-base font-semibold">Cost trends</h3>
              <span className="text-sm">
                {o.cards.cost_trends.points ? `StatCan 18-10-0289, ${o.cards.cost_trends.divisions} divisions, ${quarter(o.cards.cost_trends.since)} to ${quarter(o.cards.cost_trends.latest)}` : "No index loaded"}
              </span>
              <span className="text-xs text-slate-600">Feeds the drift checks on Rates.</span>
            </div>
            <Link href="/analytics" className={card}>
              <h3 className="text-base font-semibold">Actual costs</h3>
              <span className="text-sm">{o.cards.actual_costs.bills} bills entered on {o.cards.actual_costs.projects} project{o.cards.actual_costs.projects === 1 ? "" : "s"}</span>
              <span className="text-xs text-slate-600">Cost Intelligence: subtrade and supplier invoices against the estimate. Bills are entered on each project&apos;s Money › Costs.</span>
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
