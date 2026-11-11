"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";

import {
  buildMilestones,
  listMilestones,
  patchMilestone,
  putTeam,
  ROLE_LABEL,
  STATUS_LABEL,
  type Milestone,
  type MilestoneList,
  type MilestoneStatus,
  type RequiredDoc,
} from "@/lib/milestones";

import { EmailPanel } from "./EmailPanel";

const fmt = (d: string | null) =>
  d ? new Date(`${d}T12:00:00`).toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric" }) : "";

const DONE: MilestoneStatus[] = ["received", "done", "not_needed"];

/**
 * Milestones (wireframe E, approved by Amir 2026-10-16): for each stage of the
 * job, who has to be booked, by when, and which documents the inspector will
 * ask for. Built from the municipality's library and the project's own
 * documents; every item is the PM's to confirm, change or drop.
 */
export default function MilestonesPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<MilestoneList | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [emailFor, setEmailFor] = useState<Milestone | null>(null);
  const [showTeam, setShowTeam] = useState(false);

  const load = useCallback(() => {
    listMilestones(id).then(setData).catch((e) => setErr(String(e)));
  }, [id]);
  useEffect(load, [load]);

  const run = async (f: () => Promise<MilestoneList>) => {
    setBusy(true);
    setErr(null);
    try {
      setData({ ...(data ?? { items: [], team: [] }), ...(await f()) });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const stages = useMemo(() => {
    const out: { stage: string; items: Milestone[] }[] = [];
    for (const m of data?.items ?? []) {
      const last = out[out.length - 1];
      if (last && last.stage === m.stage) last.items.push(m);
      else out.push({ stage: m.stage, items: [m] });
    }
    return out;
  }, [data]);
  // The stage holding the earliest open item you've kept is "now". Unconfirmed
  // library items don't count: on 1234 the pre-start rows (already past) made
  // "Before start" the current stage.
  const nowStage = useMemo(() => {
    const all = (data?.items ?? []).filter((m) => !DONE.includes(m.status) && m.need_by);
    const open = all.some((m) => m.confirmed) ? all.filter((m) => m.confirmed) : all;
    open.sort((a, b) => (a.need_by ?? "").localeCompare(b.need_by ?? ""));
    return open[0]?.stage ?? null;
  }, [data]);

  if (!data) return <p className="text-sm text-slate-600">{err ?? "Loading…"}</p>;

  const facts = data.facts ?? {};

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="m-0 text-xl font-semibold">Bookings &amp; letters</h1>
          <p className="m-0 text-[13px] text-slate-600">
            Who to book, by when, and the documents each inspection needs.
            {facts.permit_number ? ` Permit ${String(facts.permit_number.value)}.` : ""}
            {facts.engineer_notice ? ` Engineer's notice: ${String(facts.engineer_notice.value)} days (from the drawing notes).` : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => setShowTeam((s) => !s)} className="rounded-md border border-slate-900 px-3 py-1.5 text-sm">
            {showTeam ? "Hide project team" : "Project team"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => run(() => buildMilestones(id))}
            className="rounded-md border border-slate-900 px-3 py-1.5 text-sm disabled:opacity-50"
            title="Adds library items this project doesn't have yet; never changes ones you've edited"
          >
            {data.items.length ? "Add missing items" : "Build the list"}
          </button>
        </div>
      </div>

      {err && <p className="m-0 text-sm text-red-700">{err}</p>}

      {showTeam && <TeamEditor id={id} data={data} onSaved={(d) => setData({ ...data, ...d })} />}

      {data.items.length === 0 ? (
        <div className="rounded-md border border-slate-300 p-6 text-sm text-slate-700">
          No milestones yet. <b>Build the list</b> adds the District of North Vancouver&apos;s inspection stages,
          the engineer and surveyor visits they depend on, and the documents each one needs, dated from this
          project&apos;s schedule. You confirm each item before it counts.
        </div>
      ) : (
        stages.map(({ stage, items }) => {
          const ready = items.filter((m) => DONE.includes(m.status)).length;
          return (
            <section key={stage} className="rounded-md border border-slate-200">
              <header className={`flex items-baseline justify-between px-4 py-2 ${stage === nowStage ? "bg-amber-50" : "bg-slate-50"}`}>
                <h2 className="m-0 text-sm font-semibold">
                  {stage === nowStage ? "NOW: " : ""}
                  {stage}
                </h2>
                <span className="text-xs text-slate-600">
                  {ready} of {items.length} done
                </span>
              </header>
              <ul className="m-0 list-none divide-y divide-slate-100 p-0">
                {items.map((m) => (
                  <Row
                    key={m.id}
                    m={m}
                    open={open === m.id}
                    onToggle={() => setOpen(open === m.id ? null : m.id)}
                    onPatch={(body) => run(() => patchMilestone(id, m.id, body))}
                    onEmail={() => setEmailFor(m)}
                    busy={busy}
                  />
                ))}
              </ul>
            </section>
          );
        })
      )}

      {emailFor && (
        <EmailPanel
          projectId={id}
          milestone={emailFor}
          onClose={() => {
            setEmailFor(null);
            load();
          }}
        />
      )}
    </div>
  );
}

function Row({
  m,
  open,
  onToggle,
  onPatch,
  onEmail,
  busy,
}: {
  m: Milestone;
  open: boolean;
  onToggle: () => void;
  onPatch: (b: Parameters<typeof patchMilestone>[2]) => void;
  onEmail: () => void;
  busy: boolean;
}) {
  const docs = m.required_docs ?? [];
  const icon = DONE.includes(m.status) ? "✓" : m.late ? "!" : m.confirmed ? "○" : "?";
  const who = m.party?.company || m.party?.name;
  const setDoc = (i: number, status: RequiredDoc["status"]) =>
    onPatch({ required_docs: docs.map((d, j) => (j === i ? { ...d, status } : d)) });

  return (
    <li className="px-4 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className={`w-4 text-center font-bold ${m.late ? "text-orange-700" : DONE.includes(m.status) ? "text-emerald-700" : "text-slate-500"}`} aria-hidden>
          {icon}
        </span>
        <button type="button" onClick={onToggle} className="min-w-[14rem] flex-1 text-left">
          <span className="font-medium">{m.title}</span>
          {!m.confirmed && <span className="ml-2 rounded border border-slate-400 px-1 text-[11px] text-slate-600">confirm</span>}
          <span className="block text-xs text-slate-600">
            {ROLE_LABEL[m.party_role] ?? m.party_role}
            {who ? ` · ${who}` : " · who? set it in Project team"}
          </span>
        </button>
        <span className={`w-36 text-xs ${m.late ? "text-orange-800" : "text-slate-600"}`}>
          {DONE.includes(m.status)
            ? STATUS_LABEL[m.status]
            : m.book_by
              ? `${m.late ? "book by (late) " : "book by "}${fmt(m.book_by)}`
              : "no schedule date"}
          {m.booked_for ? <span className="block">booked {fmt(m.booked_for)}</span> : null}
        </span>
        <span className="w-20 text-xs text-slate-600">{docs.length ? `${docs.filter((d) => d.status !== "missing").length} of ${docs.length} docs` : ""}</span>
        <select
          value={m.status}
          disabled={busy}
          onChange={(e) => onPatch({ status: e.target.value as MilestoneStatus, confirmed: true })}
          className="rounded border border-slate-300 px-1 py-0.5 text-xs"
          aria-label={`Status of ${m.title}`}
        >
          {(Object.keys(STATUS_LABEL) as MilestoneStatus[]).map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
        <button type="button" onClick={onEmail} className="rounded-md border border-slate-900 px-2 py-0.5 text-xs">
          {m.emails.some((e) => e.status === "draft" || e.status === "drafting")
            ? "Open draft"
            : m.emails.some((e) => e.status === "sent")
              ? "Email again"
              : "Draft email"}
        </button>
      </div>
      {open && (
        <div className="ml-7 mt-2 space-y-2 text-[13px] text-slate-700">
          {m.why && <p className="m-0">{m.why}</p>}
          <p className="m-0 text-xs text-slate-500">
            Source: {m.source ?? "—"}
            {m.activity_name ? ` · schedule: ${m.activity_code} ${m.activity_name} (${fmt(m.need_by)})` : " · not tied to a schedule activity"}
            {m.lead_days != null ? ` · ${m.lead_days} business days' notice` : ""}
            {m.lead_note ? ` · ${m.lead_note}` : ""}
          </p>
          {docs.length > 0 && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Documents the inspector will ask for</div>
              <ul className="m-0 list-none p-0">
                {docs.map((d, i) => (
                  <li key={d.name} className="flex items-center gap-2 py-0.5">
                    <select
                      value={d.status}
                      onChange={(e) => setDoc(i, e.target.value as RequiredDoc["status"])}
                      className="rounded border border-slate-300 px-1 py-0.5 text-xs"
                      aria-label={`${d.name} status`}
                    >
                      <option value="missing">missing</option>
                      <option value="received">received</option>
                      <option value="not_needed">not needed</option>
                    </select>
                    {d.name}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-3 text-xs">
            {!m.confirmed && (
              <button type="button" onClick={() => onPatch({ confirmed: true })} className="text-blue-700">
                Keep this item
              </button>
            )}
            <label>
              Booked for{" "}
              <input
                type="date"
                defaultValue={m.booked_for ?? ""}
                onBlur={(e) => e.target.value !== (m.booked_for ?? "") && onPatch({ booked_for: e.target.value || null, status: e.target.value ? "booked" : m.status })}
                className="rounded border border-slate-300 px-1"
              />
            </label>
          </div>
          {m.thread.length > 0 && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Emails</div>
              <ul className="m-0 list-none p-0 text-xs">
                {m.thread.map((t, i) => (
                  <li key={i}>
                    {t.direction === "out" ? `Sent to ${t.to}` : `Reply from ${t.from}`} ·{" "}
                    {new Date(t.at).toLocaleString("en-CA", { dateStyle: "medium", timeStyle: "short" })}
                    {t.document_ids.length ? ` · ${t.document_ids.length} attachment(s) filed in Documents` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function TeamEditor({ id, data, onSaved }: { id: string; data: MilestoneList; onSaved: (d: MilestoneList) => void }) {
  const used = new Set(data.items.map((m) => m.party_role));
  const roles = (data.roles ?? Object.keys(ROLE_LABEL)).filter((r) => used.has(r) || data.team.some((t) => t.role === r));
  return (
    <section className="rounded-md border border-slate-200 p-4">
      <h2 className="m-0 text-sm font-semibold">Project team</h2>
      <p className="m-0 mb-2 text-xs text-slate-600">Who each role is on this job. Drafts are addressed from here.</p>
      <div className="space-y-2">
        {roles.map((role) => (
          <TeamRow key={role} id={id} role={role} member={data.team.find((t) => t.role === role)} onSaved={onSaved} />
        ))}
      </div>
    </section>
  );
}

function TeamRow({
  id,
  role,
  member,
  onSaved,
}: {
  id: string;
  role: string;
  member?: MilestoneList["team"][number];
  onSaved: (d: MilestoneList) => void;
}) {
  const [f, setF] = useState({ name: member?.name ?? "", company: member?.company ?? "", email: member?.email ?? "", phone: member?.phone ?? "" });
  const [err, setErr] = useState<string | null>(null);
  const dirty = f.name !== (member?.name ?? "") || f.company !== (member?.company ?? "") || f.email !== (member?.email ?? "") || f.phone !== (member?.phone ?? "");
  const inp = "rounded border border-slate-300 px-2 py-1 text-sm";
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="w-52 text-xs text-slate-600">{ROLE_LABEL[role] ?? role}</span>
      <input className={`${inp} w-40`} placeholder="Company" value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} />
      <input className={`${inp} w-36`} placeholder="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <input className={`${inp} w-56`} placeholder="Email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
      <input className={`${inp} w-32`} placeholder="Phone" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} />
      {dirty && (
        <button
          type="button"
          className="rounded-md bg-slate-900 px-2 py-1 text-xs text-white"
          onClick={async () => {
            setErr(null);
            try {
              onSaved(await putTeam(id, role, f));
            } catch (e) {
              setErr(e instanceof Error ? e.message : String(e));
            }
          }}
        >
          Save
        </button>
      )}
      {err && <span className="text-xs text-red-700">{err}</span>}
    </div>
  );
}
