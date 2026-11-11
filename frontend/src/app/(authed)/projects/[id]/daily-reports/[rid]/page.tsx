"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { PhotoThumb } from "@/components/PhotoThumb";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  type DailyReport,
  type DailyReportPayload,
  type DailyTask,
  TASK_STATUSES,
  TASK_STATUS_LABEL,
  dailyReportPdfUrl,
  getDailyReport,
  submitDailyReport,
  syncDailyReport,
  updateDailyReport,
} from "@/lib/dailyReports";

type Row = Record<string, string>;

function RepeatRows({
  label, cols, rows, onChange, notMentioned = false,
}: {
  label: string;
  cols: { key: string; label: string; wide?: boolean }[];
  rows: Row[];
  onChange: (rows: Row[]) => void;
  /** The write-up recorded this section as not mentioned: blank, never "None". */
  notMentioned?: boolean;
}) {
  const set = (i: number, k: string, v: string) =>
    onChange(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  return (
    <Card className="p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{label}</h2>
        <Button variant="secondary" onClick={() => onChange([...rows, {}])} className="!px-2 !py-1 text-xs">
          + Add
        </Button>
      </div>
      {rows.length === 0 ? (
        <div className="text-xs text-slate-400">{notMentioned ? "Not mentioned, so left blank (not the same as none)." : "None."}</div>
      ) : (
        <div className="space-y-2">
          {rows.map((r, i) => (
            <div key={i} className="flex items-center gap-2">
              {cols.map((c) => (
                <Input
                  key={c.key}
                  value={r[c.key] ?? ""}
                  placeholder={c.label}
                  onChange={(e) => set(i, c.key, e.target.value)}
                  className={c.wide ? "flex-[2]" : "flex-1"}
                />
              ))}
              <button onClick={() => onChange(rows.filter((_, j) => j !== i))} className="px-1 text-slate-400 hover:text-red-600">
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function DailyReportEditor() {
  const { id, rid } = useParams<{ id: string; rid: string }>();
  const [report, setReport] = useState<DailyReport | null>(null);
  const [p, setP] = useState<DailyReportPayload>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getDailyReport(rid)
      .then((r) => {
        setReport(r);
        setP(r.payload || {});
        setDirty(false);
      })
      .catch((e) => setError(String(e)));
  }, [rid]);
  useEffect(load, [load]);

  const unsaid = (section: string) => (p.not_recorded ?? []).includes(section);
  const upd = (patch: Partial<DailyReportPayload>) => {
    setP((prev) => ({ ...prev, ...patch }));
    setDirty(true);
  };
  const setTask = (i: number, k: keyof DailyTask, v: unknown) => {
    const tasks = [...(p.tasks ?? [])];
    tasks[i] = { ...tasks[i], [k]: v } as DailyTask;
    upd({ tasks });
  };
  // A task marked complete is 100% done unless someone says otherwise (rollout test 2026-10-14).
  const setStatus = (i: number, status: string) => {
    const tasks = [...(p.tasks ?? [])];
    const t = tasks[i];
    tasks[i] = { ...t, status, ...(status === "completed" && !t.percent_complete ? { percent_complete: 100 } : {}) } as DailyTask;
    upd({ tasks });
  };
  const addTask = () =>
    upd({
      tasks: [
        ...(p.tasks ?? []),
        { activity_code: null, name: "", phase: "Ad-hoc", scheduled_start: null, scheduled_finish: null,
          ad_hoc: true, from_schedule: false, removed: false, status: "in_progress", percent_complete: 0, crew: null, notes: "" },
      ],
    });

  const finalized = report?.status === "finalized";

  const save = async () => {
    setBusy("save"); setError(null);
    try { const r = await updateDailyReport(rid, { payload: p }); setReport(r); setDirty(false); }
    catch (e) { setError(String(e)); }
    finally { setBusy(null); }
  };
  const submit = async () => {
    setBusy("submit"); setError(null);
    try { if (dirty) await updateDailyReport(rid, { payload: p }); const r = await submitDailyReport(rid); setReport(r); setDirty(false); }
    catch (e) { setError(String(e)); }
    finally { setBusy(null); }
  };
  const resync = async () => {
    setBusy("resync"); setError(null);
    try {
      if (dirty) await updateDailyReport(rid, { payload: p });
      const r = await syncDailyReport(id, report!.report_date);
      setReport(r); setP(r.payload || {}); setDirty(false);
    } catch (e) { setError(String(e)); }
    finally { setBusy(null); }
  };

  if (!report) return <div className="text-sm text-slate-400">Loading…</div>;
  const cond = p.conditions ?? {};

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <Link href={`/projects/${id}/site`} className="text-xs text-slate-500 hover:underline">
            ← Daily reports
          </Link>
          <h1 className="text-xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>
            Daily Site Report — {report.report_date}
          </h1>
          <div className="text-xs uppercase text-slate-500">{report.status}</div>
        </div>
        <div className="flex items-center gap-2">
          <a href={dailyReportPdfUrl(rid)} target="_blank" rel="noopener noreferrer"
             className="rounded border border-slate-300 px-3 py-1.5 text-xs font-medium hover:bg-slate-50">
            ⤓ PDF
          </a>
          {!finalized && (
            <>
              <Button variant="secondary" onClick={save} disabled={busy !== null || !dirty}>
                {busy === "save" ? "Saving…" : dirty ? "Save" : "Saved"}
              </Button>
              <Button onClick={submit} disabled={busy !== null}>
                {busy === "submit" ? "…" : "Submit"}
              </Button>
            </>
          )}
        </div>
      </div>

      {error && <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      {report.schedule_drifted && !finalized && (
        <div className="flex items-center justify-between rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <span>The schedule has changed since this report was generated.</span>
          <Button variant="secondary" onClick={resync} disabled={busy !== null} className="!px-2 !py-1 text-xs">
            {busy === "resync" ? "Re-syncing…" : "Re-sync from schedule"}
          </Button>
        </div>
      )}

      <Card className="p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Site Conditions</h2>
        <div className="grid grid-cols-3 gap-3">
          <Input placeholder="Weather" value={cond.weather ?? ""} onChange={(e) => upd({ conditions: { ...cond, weather: e.target.value } })} />
          <Input placeholder="Temp °C" value={String(cond.temp_c ?? "")} onChange={(e) => upd({ conditions: { ...cond, temp_c: e.target.value } })} />
          <Input placeholder="Ground / site" value={cond.ground ?? ""} onChange={(e) => upd({ conditions: { ...cond, ground: e.target.value } })} />
        </div>
      </Card>

      <Card className="p-0 overflow-hidden">
        <div className="flex items-center justify-between p-4 pb-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Work Performed (from schedule)</h2>
          <Button variant="secondary" onClick={addTask} className="!px-2 !py-1 text-xs">+ Add task</Button>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr><th className="px-3 py-2">Task</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">%</th><th className="px-3 py-2">Crew</th><th className="px-3 py-2">Notes</th></tr>
          </thead>
          <tbody>
            {(p.tasks ?? []).map((t, i) => (
              <tr key={i} className={`border-t border-slate-100 ${t.removed ? "opacity-60" : ""}`}>
                <td className="px-3 py-2">
                  <div className="flex items-center gap-1">
                    {t.is_critical && <span className="inline-block h-2 w-2 bg-[var(--color-brio-primary)]" title="Critical path" />}
                    {t.ad_hoc ? (
                      <Input value={t.name ?? ""} placeholder="Task name" onChange={(e) => setTask(i, "name", e.target.value)} className="!py-1" />
                    ) : (
                      <span className="font-medium">{t.name}</span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[10px] uppercase text-slate-400">
                    {t.phase}{t.scheduled_start ? ` · ${t.scheduled_start}–${t.scheduled_finish}` : ""}
                    {t.ad_hoc && " · added on site"}
                    {t.removed && <span className="text-red-600"> · no longer scheduled</span>}
                  </div>
                </td>
                <td className="px-3 py-2">
                  <select value={t.status} onChange={(e) => setStatus(i, e.target.value)} className="rounded border border-slate-300 px-1 py-1 text-xs">
                    {TASK_STATUSES.map((s) => <option key={s} value={s}>{TASK_STATUS_LABEL[s]}</option>)}
                  </select>
                </td>
                <td className="px-3 py-2">
                  <input type="number" min={0} max={100} value={t.percent_complete ?? 0}
                    onChange={(e) => setTask(i, "percent_complete", Number(e.target.value))}
                    className="w-14 rounded border border-slate-300 px-1 py-1 text-xs" />
                </td>
                <td className="px-3 py-2">
                  <Input value={t.crew ?? ""} onChange={(e) => setTask(i, "crew", e.target.value)} className="!py-1 w-20" />
                </td>
                <td className="px-3 py-2">
                  <Input value={t.notes ?? ""} onChange={(e) => setTask(i, "notes", e.target.value)} className="!py-1" placeholder="Notes / blockers" />
                </td>
              </tr>
            ))}
            {(p.tasks ?? []).length === 0 && (
              <tr><td colSpan={5} className="px-3 py-6 text-center text-slate-400">No scheduled tasks for this day. Add ad-hoc tasks as needed.</td></tr>
            )}
          </tbody>
        </table>
      </Card>

      <RepeatRows label="Manpower" rows={(p.manpower ?? []) as Row[]} onChange={(r) => upd({ manpower: r })} notMentioned={unsaid("manpower")}
        cols={[{ key: "trade", label: "Trade" }, { key: "company", label: "Company" }, { key: "count", label: "Crew" }, { key: "hours", label: "Hours" }]} />
      <RepeatRows label="Deliveries" rows={(p.deliveries ?? []) as Row[]} onChange={(r) => upd({ deliveries: r })} notMentioned={unsaid("deliveries")}
        cols={[{ key: "item", label: "Item", wide: true }, { key: "supplier", label: "Supplier" }, { key: "notes", label: "Notes", wide: true }]} />
      <RepeatRows label="Site Visitors" rows={(p.visitors ?? []) as Row[]} onChange={(r) => upd({ visitors: r })} notMentioned={unsaid("visitors")}
        cols={[{ key: "name", label: "Name" }, { key: "company", label: "Company" }, { key: "purpose", label: "Purpose", wide: true }]} />
      <RepeatRows label="Delays & Blockers" rows={(p.delays ?? []) as Row[]} onChange={(r) => upd({ delays: r })} notMentioned={unsaid("delays")}
        cols={[{ key: "reason", label: "Reason", wide: true }, { key: "impact", label: "Impact" }, { key: "notes", label: "Notes", wide: true }]} />

      <Card className="p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Health &amp; Safety</h2>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!p.safety?.incident} onChange={(e) => upd({ safety: { ...(p.safety ?? {}), incident: e.target.checked } })} />
          Incident to report
        </label>
        <textarea value={p.safety?.notes ?? ""} onChange={(e) => upd({ safety: { ...(p.safety ?? {}), notes: e.target.value } })}
          rows={2} placeholder="Safety notes…" className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm" />
      </Card>

      {(p.raised_for_office ?? []).length > 0 && (
        <Card className="p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Raised for the office</h2>
          <ul className="m-0 list-disc space-y-1 pl-5 text-sm">
            {(p.raised_for_office ?? []).map((r, i) => (
              <li key={i}>{r.what}{r.why && <span className="text-slate-500"> ({r.why})</span>}</li>
            ))}
          </ul>
        </Card>
      )}

      {(p.photos ?? []).length > 0 && (
        <Card className="p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">Photos</h2>
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
            {(p.photos ?? []).map((id) => <PhotoThumb key={id} id={id} name="Site photo" className="h-28 w-full" />)}
          </div>
        </Card>
      )}

      <Card className="p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">General Notes</h2>
        <textarea value={p.notes ?? ""} onChange={(e) => upd({ notes: e.target.value })} rows={4}
          placeholder="End-of-day summary, coordination notes, anything affecting upcoming days…"
          className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm" />
      </Card>
    </div>
  );
}
