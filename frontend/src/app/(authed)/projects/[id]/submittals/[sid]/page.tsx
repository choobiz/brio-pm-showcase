"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { getPreviewUrl, listDocuments, type DocumentRead } from "@/lib/documents";
import { TRADE_CATEGORIES, TRADE_CATEGORY_LABELS } from "@/lib/trades";
import { listMilestones, type Milestone } from "@/lib/milestones";
import {
  getCitation,
  getSubmittal,
  newRevision,
  OUTCOMES,
  patchSubmittal,
  quoteLineToLibrary,
  RESULT_LABEL,
  rerun,
  setCall,
  suggest,
  type Call,
  type Citation as Cite,
  type Finding,
  type Outcome,
  type Party,
  type QuoteLine,
  type SubmittalDetail,
} from "@/lib/submittals";

import { QuestionEmail } from "./QuestionEmail";

const CHIP: Record<string, string> = {
  critical: "bg-red-900 text-white",
  confirm: "bg-orange-100 text-orange-900",
  not_shown: "bg-orange-100 text-orange-900",
  match: "bg-emerald-50 text-emerald-900",
};
const PREVIOUS: Record<string, string> = { fixed: "Fixed since last rev", still_open: "Still open", new: "New this rev" };

/**
 * One submittal (wireframes C and D, approved 2026-10-17): the findings with
 * their citations, the quote, then the questions grouped by who answers, the
 * outcome and the three ticks before ordering. Emails leave by Copy to Gmail
 * only; "cleared to order" is a marker, Keystone orders nothing.
 */
export default function SubmittalPage({ params }: { params: Promise<{ id: string; sid: string }> }) {
  const { id, sid } = use(params);
  const [s, setS] = useState<SubmittalDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cite, setCite] = useState<{ fid: string; side: "shop" | "plan" } | null>(null);
  const [milestones, setMilestones] = useState<Milestone[]>([]);

  const load = useCallback(() => {
    getSubmittal(id, sid)
      .then(setS)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [id, sid]);
  useEffect(load, [load]);
  useEffect(() => {
    listMilestones(id)
      .then((m) => setMilestones(m.items))
      .catch(() => setMilestones([]));
  }, [id]);
  // While the reviewer reads, look again every few seconds.
  useEffect(() => {
    if (s?.status !== "reviewing") return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [s?.status, load]);

  const act = async (f: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await f();
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!s) return <p className="text-sm text-slate-500">{err ?? "Loading…"}</p>;

  const locked = !!s.outcome || s.status === "superseded";
  const openCite = (fid: string, side: "shop" | "plan") => setCite({ fid, side });

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Link href={`/projects/${id}/submittals`} className="text-sm text-blue-700">
            Back to Submittals
          </Link>
          <h1 className="m-0 text-2xl font-semibold">
            {s.name} · {s.revision} <span className="text-sm font-normal text-slate-500">{s.ref}</span>
          </h1>
          <p className="m-0 text-sm text-slate-600">
            {s.supplier ? `${s.supplier} · ` : ""}
            {s.submittal_docs.map((d) => d.filename).join(", ")} checked against {s.governing_docs.map((d) => d.filename).join(", ")}
            {s.reviewed_at ? ` · run ${new Date(s.reviewed_at).toLocaleDateString("en-CA", { month: "short", day: "numeric" })}` : ""}
            {s.cost_usd != null ? ` · cost $${s.cost_usd.toFixed(2)}` : ""}
          </p>
          {s.revisions.length > 1 && (
            <p className="m-0 mt-1 flex flex-wrap gap-2 text-xs">
              {s.revisions.map((r) => (
                <Link
                  key={r.id}
                  href={`/projects/${id}/submittals/${r.id}`}
                  className={`rounded border px-2 py-0.5 ${r.id === s.id ? "border-slate-900" : "border-slate-300 text-slate-600"}`}
                >
                  {r.revision}
                </Link>
              ))}
            </p>
          )}
        </div>
        {s.findings.length > 0 && (
          <div className="flex gap-2">
            <Tile n={s.counts.critical} label="Critical" cls="bg-red-900 text-white" />
            <Tile n={s.counts.confirm + s.counts.not_shown} label="Confirm" cls="bg-orange-100 text-orange-900" />
            <Tile n={s.counts.match} label="Match" cls="bg-emerald-50 text-emerald-900" />
          </div>
        )}
      </div>

      {s.status === "superseded" && (
        <p className="m-0 rounded border border-slate-300 bg-slate-50 p-3 text-sm">
          A newer revision replaced this one. It stays here as the trail.
        </p>
      )}
      {s.status === "reviewing" && <p className="m-0 rounded border border-slate-200 bg-white p-4 text-sm">Reading the files and checking…</p>}
      {s.status === "review_failed" && (
        <div className="rounded border border-red-200 bg-red-50 p-4 text-sm">
          <p className="m-0 text-red-800">{s.error ?? "The review didn't finish."}</p>
          <button
            type="button"
            disabled={busy}
            onClick={() => act(() => rerun(id, sid, s.approved_usd ?? 1))}
            className="mt-2 rounded-md bg-slate-900 px-3 py-1.5 text-white disabled:opacity-50"
          >
            Run it again (up to ${(s.approved_usd ?? 1).toFixed(2)})
          </button>
        </div>
      )}
      {s.summary && <p className="m-0 text-sm">{s.summary}</p>}

      {s.findings.length > 0 && (
        <div className="flex flex-wrap items-start gap-5">
          <div className="min-w-0 flex-[999_1_640px] overflow-x-auto rounded-md border border-slate-200 bg-white">
            <table className="w-full min-w-[760px] text-[13px]">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2">Check</th>
                  <th className="px-3 py-2">Shop drawing says</th>
                  <th className="px-3 py-2">Plan says</th>
                  <th className="px-3 py-2">Result</th>
                  <th className="px-3 py-2">Your call</th>
                </tr>
              </thead>
              <tbody>
                {s.findings.map((f) => (
                  <FindingRow
                    key={`${f.id}-${s.reviewed_at ?? ""}-${f.call ?? ""}`}
                    f={f}
                    locked={locked}
                    busy={busy}
                    onCite={(side) => openCite(f.id, side)}
                    onCall={(body) => act(() => setCall(id, sid, f.id, body))}
                  />
                ))}
              </tbody>
            </table>
          </div>

          <aside className="flex w-full flex-[1_1_300px] flex-col gap-4">
            {s.quote?.total != null && (
              <div className="flex flex-col gap-1.5 rounded-md border border-slate-200 bg-white p-4 text-[13px]">
                <div className="text-base font-semibold">The quote</div>
                <Line k={`${s.quote.supplier ?? ""} ${s.quote.number ?? ""}${s.quote.date ? `, ${s.quote.date}` : ""}`} v="" />
                {s.quote.subtotal != null && <Line k="Subtotal" v={money(s.quote.subtotal)} />}
                {s.quote.tax != null && <Line k="Tax" v={money(s.quote.tax)} />}
                <Line k="Total" v={money(s.quote.total)} strong />
                {s.quote.bom_matches === true && (
                  <span className="rounded bg-emerald-50 px-2 py-1 text-emerald-900">Parts list matches the drawing.</span>
                )}
                {s.quote.bom_matches === false && (
                  <span className="rounded bg-orange-100 px-2 py-1 text-orange-900">Parts list doesn&apos;t match the drawing.</span>
                )}
                {(s.quote.notes ?? []).map((n) => (
                  <span key={n} className="rounded bg-orange-100 px-2 py-1 text-orange-900">
                    {n}
                  </span>
                ))}
                {(s.quote.lines ?? []).length > 0 && (
                  <QuoteLines lines={s.quote.lines!} trade={s.trade} onAdd={(i, body) => act(() => quoteLineToLibrary(id, sid, i, body))} busy={busy} />
                )}
              </div>
            )}
            <Citation projectId={id} sid={sid} cite={cite} />
            <a href="#decide" className="rounded-md bg-slate-900 px-4 py-2.5 text-center text-sm font-medium text-white">
              Questions and outcome ({s.findings.filter((f) => f.call === "ask").length} to ask)
            </a>
          </aside>
        </div>
      )}

      {s.findings.length > 0 && (
        <Decide s={s} id={id} sid={sid} busy={busy} act={act} milestones={milestones} locked={locked} onCopied={load} />
      )}

      {err && <p className="text-sm text-red-700">{err}</p>}
    </div>
  );
}

function Tile({ n, label, cls }: { n: number; label: string; cls: string }) {
  return (
    <div className={`flex flex-col rounded-md px-3 py-2 ${cls}`}>
      <strong className="text-xl">{n}</strong>
      <span className="text-xs">{label}</span>
    </div>
  );
}

function Line({ k, v, strong }: { k: string; v: string; strong?: boolean }) {
  return (
    <div className="flex justify-between gap-2">
      <span className={strong ? "font-semibold" : "text-slate-600"}>{k}</span>
      <span className={strong ? "font-semibold" : ""}>{v}</span>
    </div>
  );
}

const money = (n: number | null | undefined) =>
  n == null ? "" : n.toLocaleString("en-CA", { style: "currency", currency: "CAD", minimumFractionDigits: 2 });

function FindingRow({
  f,
  locked,
  busy,
  onCite,
  onCall,
}: {
  f: Finding;
  locked: boolean;
  busy: boolean;
  onCite: (side: "shop" | "plan") => void;
  onCall: (body: { call: Call | null; call_note?: string | null; question?: string; ask_who?: Party }) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [q, setQ] = useState(f.question);
  const [who, setWho] = useState<Party>(f.ask_who === "engineer" ? "engineer" : "supplier");
  const [noteText, setNoteText] = useState(f.call_note ?? "");
  const open = f.result !== "match";
  const btn = "rounded border px-2 py-1 text-xs disabled:opacity-50";
  return (
    <>
      <tr className="border-t border-slate-100 align-top">
        <td className="px-3 py-2.5 font-semibold">{f.check}</td>
        <td className="px-3 py-2.5">
          <div>{f.shop}</div>
          {f.shop_ref && (
            <button type="button" onClick={() => onCite("shop")} className="font-mono text-[11px] text-blue-700 hover:underline">
              {f.shop_ref}
            </button>
          )}
        </td>
        <td className="px-3 py-2.5">
          <div>{f.plan}</div>
          {f.plan_ref && (
            <button type="button" onClick={() => onCite("plan")} className="font-mono text-[11px] text-blue-700 hover:underline">
              {f.plan_ref}
            </button>
          )}
        </td>
        <td className="px-3 py-2.5">
          <span className={`rounded px-2 py-0.5 text-xs ${CHIP[f.result]}`}>{RESULT_LABEL[f.result]}</span>
          {f.previous && <div className="mt-1 text-[11px] text-slate-600">{PREVIOUS[f.previous]}</div>}
          {f.why && <div className="mt-1 text-[11px] text-slate-500">{f.why}</div>}
        </td>
        <td className="px-3 py-2.5">
          {!open ? (
            <span className="text-slate-500">No action</span>
          ) : locked ? (
            <span className="text-slate-600">{f.call ? { ask: "Asked", fine: "Fine as is", answered: "Answered" }[f.call] : "Undecided"}</span>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                disabled={busy}
                onClick={() => setEditing(true)}
                className={`${btn} ${f.call === "ask" ? "border-slate-900 bg-slate-900 text-white" : "border-slate-900"}`}
              >
                {f.call === "ask" ? "Asking" : "Ask"}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => onCall({ call: f.call === "fine" ? null : "fine" })}
                className={`${btn} ${f.call === "fine" ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300"}`}
              >
                Fine as is
              </button>
              {f.call === "ask" && (
                <button type="button" disabled={busy} onClick={() => setEditing(true)} className={`${btn} border-slate-300`}>
                  Answered…
                </button>
              )}
              {f.call === "answered" && <span className="text-xs text-emerald-800">Answered{f.call_note ? `: ${f.call_note}` : ""}</span>}
            </div>
          )}
        </td>
      </tr>
      {editing && !locked && (
        <tr className="bg-slate-50">
          <td colSpan={5} className="px-3 py-3">
            <div className="flex flex-col gap-2 text-[13px]">
              <label>
                The question
                <textarea rows={2} value={q} onChange={(e) => setQ(e.target.value)} className="w-full rounded border border-slate-300 px-2 py-1" />
              </label>
              <div className="flex flex-wrap items-center gap-3">
                <span>Who answers:</span>
                {(["engineer", "supplier"] as Party[]).map((p) => (
                  <label key={p} className="flex items-center gap-1">
                    <input type="radio" checked={who === p} onChange={() => setWho(p)} /> {p === "engineer" ? "Engineer" : "Supplier"}
                  </label>
                ))}
              </div>
              {f.call === "ask" && (
                <label>
                  Their answer (marks it answered)
                  <input value={noteText} onChange={(e) => setNoteText(e.target.value)} className="w-full rounded border border-slate-300 px-2 py-1" />
                </label>
              )}
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={busy || !q.trim()}
                  onClick={() => {
                    onCall({ call: "ask", question: q.trim(), ask_who: who });
                    setEditing(false);
                  }}
                  className="rounded-md bg-slate-900 px-3 py-1 text-white disabled:opacity-50"
                >
                  Ask this
                </button>
                {f.call === "ask" && (
                  <button
                    type="button"
                    disabled={busy || !noteText.trim()}
                    onClick={() => {
                      onCall({ call: "answered", call_note: noteText.trim() });
                      setEditing(false);
                    }}
                    className="rounded-md border border-slate-900 px-3 py-1 disabled:opacity-50"
                  >
                    Mark answered
                  </button>
                )}
                <button type="button" onClick={() => setEditing(false)} className="px-2 text-slate-600">
                  Cancel
                </button>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function Citation({
  projectId,
  sid,
  cite,
}: {
  projectId: string;
  sid: string;
  cite: { fid: string; side: "shop" | "plan" } | null;
}) {
  const [c, setC] = useState<Cite | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    setC(null);
    setErr(null);
    if (!cite) return;
    setLoading(true);
    getCitation(projectId, sid, cite.fid, cite.side)
      .then(setC)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [cite, projectId, sid]);
  const openFile = async () => {
    if (!c) return;
    const p = await getPreviewUrl(c.document.id);
    if (p.preview_url) window.open(`${p.preview_url}${c.page ? `#page=${c.page}` : ""}`, "_blank", "noopener");
  };
  return (
    <div className="flex flex-col gap-2 rounded-md border border-slate-200 bg-white p-4 text-[13px]">
      <div className="text-base font-semibold">Open a citation</div>
      {!cite && <div className="rounded bg-slate-50 p-4 text-center text-slate-500">Click a sheet reference to open that page here, with what it cites boxed.</div>}
      {loading && <div className="text-slate-500">Finding it on the page…</div>}
      {c && (
        <>
          <div className="text-xs text-slate-600">
            {c.ref} → {c.document.filename}
            {c.page ? `, page ${c.page}${c.pages ? ` of ${c.pages}` : ""}` : ""}
          </div>
          {c.image && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={`data:${c.media_type};base64,${c.image}`} alt={`${c.document.filename} page ${c.page ?? ""}`} className="w-full rounded border border-slate-200" />
          )}
          <div className="text-xs">
            {c.found.length > 0 ? (
              <span className="text-slate-700">Boxed: {c.found.join(", ")}</span>
            ) : (
              <span className="text-slate-500">Looked for: {c.searched.join(", ") || "nothing to look for"}</span>
            )}
          </div>
          {c.note && <div className="text-xs text-orange-900">{c.note}</div>}
          <button type="button" onClick={openFile} className="self-start text-xs text-blue-700 hover:underline">
            Open the whole file
          </button>
        </>
      )}
      {err && <div className="text-red-700">{err}</div>}
    </div>
  );
}

const TRADE_FOR: Record<string, string> = {
  stormwater: "stormwater",
  trusses: "framing",
  windows: "windows_doors",
  rebar: "concrete",
  hvac: "hvac",
};

function QuoteLines({
  lines,
  trade,
  onAdd,
  busy,
}: {
  lines: QuoteLine[];
  trade: string;
  onAdd: (i: number, body: { name: string; unit: string; unit_cost: number; trade: string; sku: string | null }) => void;
  busy: boolean;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const [form, setForm] = useState({ name: "", unit: "ea", cost: "", trade: TRADE_FOR[trade] ?? "other" });
  const inp = "w-full rounded border border-slate-300 px-2 py-1";
  return (
    <div className="mt-1 flex flex-col gap-1 border-t border-slate-100 pt-2">
      <div className="font-semibold">Lines</div>
      {lines.map((l) => (
        <div key={l.i} className="flex flex-col gap-1 border-b border-slate-50 pb-1">
          <div className="flex justify-between gap-2">
            <span>
              {l.qty != null ? `${l.qty} ${l.unit ?? ""} · ` : ""}
              {l.description}
            </span>
            <span className="whitespace-nowrap">{l.unit_price != null ? money(l.unit_price) : ""}</span>
          </div>
          {l.material_id ? (
            <span className="text-xs text-emerald-800">In the rate library</span>
          ) : open === l.i ? (
            <div className="flex flex-col gap-1 rounded bg-slate-50 p-2 text-xs">
              <label>
                Name in the library
                <input className={inp} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </label>
              <div className="flex gap-2">
                <label className="w-20">
                  Unit
                  <input className={inp} value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })} />
                </label>
                <label className="flex-1">
                  Unit cost
                  <input className={inp} inputMode="decimal" value={form.cost} onChange={(e) => setForm({ ...form, cost: e.target.value })} />
                </label>
              </div>
              <label>
                Trade
                <select className={inp} value={form.trade} onChange={(e) => setForm({ ...form, trade: e.target.value })}>
                  {TRADE_CATEGORIES.map((t) => (
                    <option key={t} value={t}>
                      {TRADE_CATEGORY_LABELS[t]}
                    </option>
                  ))}
                </select>
              </label>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={busy || !form.name.trim() || !(Number(form.cost) > 0)}
                  onClick={() => {
                    onAdd(l.i, { name: form.name.trim(), unit: form.unit, unit_cost: Number(form.cost), trade: form.trade, sku: l.sku });
                    setOpen(null);
                  }}
                  className="rounded bg-slate-900 px-2 py-1 text-white disabled:opacity-50"
                >
                  Add at {Number(form.cost) > 0 ? money(Number(form.cost)) : "$—"}/{form.unit}
                </button>
                <button type="button" onClick={() => setOpen(null)} className="px-1 text-slate-600">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => {
                setForm({ name: l.description, unit: l.unit ?? "ea", cost: l.unit_price != null ? String(l.unit_price) : "", trade: TRADE_FOR[trade] ?? "other" });
                setOpen(l.i);
              }}
              className="self-start text-xs text-blue-700 hover:underline"
            >
              Add to rate library
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function Decide({
  s,
  id,
  sid,
  busy,
  act,
  milestones,
  locked,
  onCopied,
}: {
  s: SubmittalDetail;
  id: string;
  sid: string;
  busy: boolean;
  act: (f: () => Promise<unknown>) => void;
  milestones: Milestone[];
  locked: boolean;
  onCopied: () => void;
}) {
  const [outcome, setOutcome] = useState<Outcome | "">(s.outcome ?? "");
  const [note, setNote] = useState(s.outcome_note ?? "");
  const byId = Object.fromEntries(s.findings.map((f) => [f.id, f]));
  const parties = (["engineer", "supplier"] as Party[]).filter((p) => s.questions[p]?.length);
  const who = (p: Party) =>
    p === "engineer"
      ? [s.parties.engineer.company, s.parties.engineer.name].filter(Boolean).join(" · ") || "The engineer (set them in Milestones › Project team)"
      : s.parties.supplier.company || "The supplier";

  return (
    <section id="decide" className="space-y-4 border-t border-slate-200 pt-5">
      <div>
        <h2 className="m-0 text-xl font-semibold">Questions and outcome</h2>
        <p className="m-0 text-sm text-slate-600">
          The items you marked Ask, grouped by who answers them. Each group becomes one email you send from Gmail.
        </p>
      </div>
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 flex-[999_1_560px] flex-col gap-4">
          {parties.length === 0 && (
            <p className="m-0 rounded border border-dashed border-slate-300 p-4 text-sm text-slate-600">
              Nothing marked Ask yet. Mark the findings above Ask or Fine as is.
            </p>
          )}
          {parties.map((p) => (
            <div key={p} className="flex flex-col gap-2 rounded-md border border-slate-200 bg-white p-4">
              <div className="flex items-baseline justify-between">
                <strong>{who(p)}</strong>
                <span className="text-xs text-slate-500">
                  {s.questions[p]!.length} question{s.questions[p]!.length === 1 ? "" : "s"}
                </span>
              </div>
              <ol className="m-0 space-y-1 pl-5 text-[13px]">
                {s.questions[p]!.map((fid) => (
                  <li key={fid}>{byId[fid]?.question}</li>
                ))}
              </ol>
              <QuestionEmail projectId={id} sid={sid} refTag={s.ref} party={p} questionIds={s.questions[p]!} onCopied={onCopied} />
            </div>
          ))}
          {s.thread.length > 0 && (
            <div className="rounded-md border border-slate-200 bg-white p-4 text-[13px]">
              <strong>Emails filed here</strong>
              {s.thread.map((t, i) => (
                <div key={i} className="text-slate-600">
                  {new Date(t.at).toLocaleDateString("en-CA", { month: "short", day: "numeric" })} ·{" "}
                  {t.direction === "out" ? "sent" : `reply from ${t.from}`} · {t.subject}
                </div>
              ))}
            </div>
          )}
        </div>

        <aside className="flex w-full flex-[1_1_340px] flex-col gap-4 text-[13px]">
          <div className="flex flex-col gap-2 rounded-md border border-slate-200 bg-white p-4">
            <strong className="text-base">Outcome</strong>
            <span className="text-xs text-slate-500">What goes back to the supplier. Recorded with the date and who decided.</span>
            {OUTCOMES.map((o) => (
              <label key={o.value} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="outcome"
                  disabled={s.status === "superseded"}
                  checked={outcome === o.value}
                  onChange={() => setOutcome(o.value)}
                />
                {o.label}
              </label>
            ))}
            <label>
              Note
              <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} className="w-full rounded border border-slate-300 px-2 py-1" />
            </label>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy || !outcome || s.status === "superseded"}
                onClick={() => act(() => patchSubmittal(id, sid, { outcome: outcome || null, outcome_note: note }))}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-white disabled:opacity-50"
              >
                Record outcome
              </button>
              {s.outcome && s.status !== "superseded" && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setOutcome("");
                    act(() => patchSubmittal(id, sid, { outcome: null }));
                  }}
                  className="rounded-md border border-slate-300 px-3 py-1.5"
                >
                  Clear
                </button>
              )}
            </div>
            {s.outcome_at && (
              <span className="text-xs text-slate-500">
                {OUTCOMES.find((o) => o.value === s.outcome)?.label} · {s.outcome_by} ·{" "}
                {new Date(s.outcome_at).toLocaleDateString("en-CA", { month: "short", day: "numeric" })}
              </span>
            )}
          </div>

          <div className="flex flex-col gap-2 rounded-md border border-slate-200 bg-white p-4">
            <strong className="text-base">Before the order is placed</strong>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={s.signed_received}
                disabled={busy}
                onChange={(e) => act(() => patchSubmittal(id, sid, { signed_received: e.target.checked }))}
              />
              Engineer&apos;s signed review received
            </label>
            <label className="flex items-center gap-2 text-slate-700">
              <input type="checkbox" checked={s.gate.no_open_critical} disabled readOnly /> No Critical findings left open
            </label>
            <label className="flex items-center gap-2 text-slate-700">
              <input type="checkbox" checked={s.gate.outcome_ok} disabled readOnly /> Outcome is &quot;no exceptions&quot; or &quot;as noted&quot;
            </label>
            <span className={s.gate.cleared ? "font-semibold text-emerald-800" : "text-slate-500"}>
              {s.gate.cleared ? "Cleared to order." : "When all three are ticked, it is marked cleared to order. Keystone doesn't place the order."}
            </span>
          </div>

          <div className="flex flex-col gap-2 rounded-md border border-slate-200 bg-white p-4">
            <strong className="text-base">Tied to</strong>
            <label>
              Milestone
              <select
                value={s.milestone_id ?? ""}
                disabled={busy}
                onChange={(e) => act(() => patchSubmittal(id, sid, { milestone_id: e.target.value || null }))}
                className="w-full rounded border border-slate-300 px-2 py-1"
              >
                <option value="">None</option>
                {milestones.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.ref} · {m.title}
                  </option>
                ))}
              </select>
            </label>
            <span className="text-slate-600">
              Documents: {[...s.submittal_docs, ...s.governing_docs].map((d) => d.filename).join(", ")}
            </span>
          </div>

          {!locked || s.outcome === "revise_resubmit" ? <Revision s={s} id={id} sid={sid} /> : null}

          <div className="flex flex-col gap-1 rounded-md border border-slate-200 bg-white p-4">
            <strong className="text-base">History</strong>
            {[...s.history].reverse().map((h, i) => (
              <span key={i} className="text-slate-600">
                {new Date(h.at).toLocaleDateString("en-CA", { month: "short", day: "numeric" })} · {h.text}
              </span>
            ))}
          </div>
        </aside>
      </div>
    </section>
  );
}

function Revision({ s, id, sid }: { s: SubmittalDetail; id: string; sid: string }) {
  const [open, setOpen] = useState(false);
  const [docs, setDocs] = useState<DocumentRead[]>([]);
  const [pick, setPick] = useState<Set<string>>(new Set());
  const [rev, setRev] = useState(() => {
    const n = Number(s.revision.match(/\d+/)?.[0] ?? 0);
    return `REV ${n + 1}`;
  });
  const [cost, setCost] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = Array.from(pick).sort().join(",");
  useEffect(() => {
    if (open) listDocuments(id).then(setDocs).catch(() => setDocs([]));
  }, [open, id]);
  useEffect(() => {
    if (!open || !key) return;
    suggest(id, { trade: s.trade, submittal_doc_ids: key.split(","), governing_doc_ids: s.governing_docs.map((d) => d.id) })
      .then((r) => setCost(r.cost.usd))
      .catch(() => setCost(null));
  }, [open, key, id, s.trade, s.governing_docs]);
  if (s.status === "superseded") return null;
  if (!open)
    return (
      <button type="button" onClick={() => setOpen(true)} className="rounded-md border border-slate-900 px-3 py-2 text-left">
        A new revision came in: check it against this one
      </button>
    );
  return (
    <div className="flex flex-col gap-2 rounded-md border border-slate-200 bg-white p-4">
      <strong className="text-base">New revision</strong>
      <label>
        Revision
        <input value={rev} onChange={(e) => setRev(e.target.value)} className="w-full rounded border border-slate-300 px-2 py-1" />
      </label>
      <div className="max-h-44 overflow-y-auto">
        {docs.map((d) => (
          <label key={d.id} className="flex items-start gap-2 py-0.5">
            <input
              type="checkbox"
              checked={pick.has(d.id)}
              onChange={() =>
                setPick((x) => {
                  const n = new Set(x);
                  if (n.has(d.id)) n.delete(d.id);
                  else n.add(d.id);
                  return n;
                })
              }
            />
            {d.filename}
          </label>
        ))}
      </div>
      <span className="text-xs text-slate-500">Upload the new revision in Documents first, then tick it here.</span>
      <button
        type="button"
        disabled={busy || !pick.size || !cost}
        onClick={async () => {
          setBusy(true);
          setErr(null);
          try {
            const r = await newRevision(id, sid, { revision: rev, submittal_doc_ids: Array.from(pick), approve_usd: cost! });
            window.location.href = `/projects/${id}/submittals/${r.id}`;
          } catch (e) {
            setErr(e instanceof Error ? e.message : String(e));
            setBusy(false);
          }
        }}
        className="rounded-md bg-slate-900 px-3 py-1.5 text-white disabled:opacity-50"
      >
        {cost ? `Review ${rev} (up to $${cost.toFixed(2)})` : `Review ${rev}`}
      </button>
      {err && <span className="text-red-700">{err}</span>}
    </div>
  );
}
