"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { acceptProposal, getProposal, proposalPdfUrl, type PortalProposal } from "@/lib/portal";

import { usePortal } from "../portalCtx";

/** Portal › Proposal (round 2, 2026-11-02): the proposal BRIO sent, with
 *  Accept (type your name) or Ask a question (goes to Messages). Accepting is
 *  recorded; BRIO then prepares the contract for signature. */

function money(n: number | null): string {
  return n == null ? "—" : n.toLocaleString("en-CA", { style: "currency", currency: "CAD" });
}
function day(s: string | null): string {
  if (!s) return "";
  const [y, m, d] = s.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-CA", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
}

export default function ProposalPage() {
  const { project, loading } = usePortal();
  const [p, setP] = useState<PortalProposal | null | undefined>(undefined);
  const [name, setName] = useState("");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [dropped, setDropped] = useState<string[]>([]);

  useEffect(() => {
    if (project) getProposal(project.id).then((r) => setP(r.proposal), () => setP(null));
  }, [project]);

  async function accept() {
    if (!project || !p) return;
    setBusy(true);
    setErr(null);
    try {
      setP(await acceptProposal(project.id, p.id, name, p.options.length ? p.options.filter((o) => !dropped.includes(o.name)).map((o) => o.name) : undefined));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (loading || !project || p === undefined) return <div className="h-40" />;
  if (p === null) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
        No proposal waiting for you right now. When we send one, you&apos;ll get an email and it will appear here.
      </div>
    );
  }

  // Options start included (the proposal's price includes them); untick to drop.
  const kept = p.options.filter((o) => !dropped.includes(o.name));
  const sub = p.options.length ? Math.round((p.base + kept.reduce((n, o) => n + o.amount, 0)) * 100) / 100 : p.subtotal;
  const gst = p.options.length ? Math.round((sub ?? 0) * 5) / 100 : p.gst;
  const total = p.options.length ? Math.round(((sub ?? 0) + (gst ?? 0)) * 100) / 100 : p.total;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">Your proposal</h1>
        {p.headline && <p className="mt-1 text-slate-600">{p.headline}</p>}
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <dl className="grid gap-2 text-sm sm:grid-cols-3">
          <div><dt className="text-slate-500">Before GST</dt><dd className="text-lg font-medium">{money(p.accepted ? p.subtotal : sub)}</dd></div>
          <div><dt className="text-slate-500">GST</dt><dd className="text-lg font-medium">{money(p.accepted ? p.gst : gst)}</dd></div>
          <div><dt className="text-slate-500">Total</dt><dd className="text-lg font-semibold">{money(p.accepted ? (p.accepted.total ?? p.total) : total)}</dd></div>
        </dl>
        {p.options.length > 0 && !p.accepted && (
          <fieldset className="mt-4 space-y-2 border-t border-slate-100 pt-3">
            <legend className="text-sm font-semibold">Options</legend>
            {p.options.map((o) => (
              <label key={o.name} className="flex items-center gap-2 text-sm">
                <input type="checkbox" className="h-4 w-4" checked={!dropped.includes(o.name)}
                  onChange={(e) => setDropped((d) => (e.target.checked ? d.filter((x) => x !== o.name) : [...d, o.name]))} />
                <span className="flex-1">{o.name}</span><span className="tabular-nums">{money(o.amount)}</span>
              </label>
            ))}
          </fieldset>
        )}
        {p.valid_until && !p.accepted && (
          <p className={`mt-3 text-sm ${p.expired ? "text-amber-800" : "text-slate-600"}`}>
            {p.expired ? `This price was valid until ${day(p.valid_until)}.` : `Price valid until ${day(p.valid_until)}.`}
          </p>
        )}
        <a href={proposalPdfUrl(p)} target="_blank" rel="noopener noreferrer"
          className="mt-4 inline-flex min-h-11 items-center rounded-lg border border-slate-900 px-4 text-sm font-medium">
          Read the full proposal (PDF)
        </a>
      </section>

      {p.accepted ? (
        <section className="rounded-xl border border-green-700 bg-white p-5 text-sm" role="status">
          <b className="text-green-800">Accepted</b> by {p.accepted.typed_name} on {new Date(p.accepted.accepted_at).toLocaleString("en-CA", { dateStyle: "long", timeStyle: "short", timeZone: "America/Vancouver" })}.
          We&apos;ll send the contract for signature next.
        </section>
      ) : p.expired ? (
        <section className="rounded-xl border border-slate-200 bg-white p-5 text-sm">
          <Link href="/portal/messages" className="font-medium text-blue-700">Message us</Link> for an updated proposal.
        </section>
      ) : (
        <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="text-base font-semibold">Accept this proposal</h2>
          <label className="block text-sm">
            Type your full name
            <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name"
              className="mt-1 block w-full max-w-sm rounded-lg border border-slate-300 px-3 py-2 text-base" />
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-1 h-4 w-4" />
            <span>I accept this proposal at {money(total)} including GST{p.options.length && dropped.length ? `, without ${dropped.join(", ")}` : ""}. BRIO will prepare the contract for us to sign.</span>
          </label>
          {err && <p className="text-sm text-red-700">{err}</p>}
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" onClick={accept} disabled={busy || !agree || name.trim().length < 2}
              className="min-h-11 rounded-lg bg-slate-900 px-5 text-sm font-medium text-white disabled:opacity-50">
              {busy ? "Accepting…" : "Accept"}
            </button>
            <Link href="/portal/messages" className="text-sm font-medium text-blue-700">Ask a question first</Link>
          </div>
        </section>
      )}
    </div>
  );
}
