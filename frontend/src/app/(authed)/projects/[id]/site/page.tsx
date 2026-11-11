"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useRef, useState } from "react";

import { PhotoThumb } from "@/components/PhotoThumb";
import { JobProgress } from "@/components/JobProgress";
import { Box, Label } from "@/components/kit";
import { VoiceInput } from "@/components/VoiceInput";
import { WorkspaceError } from "@/components/WorkspaceError";
import {
  dailyReportPdfUrl,
  listDailyReports,
  submitDailyReport,
  syncDailyReport,
  updateDailyReport,
  type DailyReport,
  type DailyReportPayload,
} from "@/lib/dailyReports";
import { listDocuments, uploadDocument, type DocumentRead } from "@/lib/documents";
import { getSiteDay, type DayStatus, type SiteDay } from "@/lib/site";
import {
  createCapture,
  getCapture,
  mergeCapture,
  type CaptureJob,
  type CaptureProposal,
} from "@/lib/workspaceDailyCapture";

import ProjectHours from "./ProjectHours";

/**
 * Site — the office view of what the crew files from the phone (round-1
 * wireframe). Daily reports: a week strip, the chosen day with what was
 * scheduled, "how did the day go?" written up by the AI and checked before it
 * is saved, and the day's hours and photos beside it. Hours and Photos are the
 * full lists.
 *
 * Nothing here creates a report until someone saves one: the day view is read
 * from /site-day, which writes nothing.
 */

const STATUS: Record<DayStatus, { label: string; cls: string }> = {
  filed: { label: "Filed", cls: "text-emerald-800" },
  draft: { label: "Draft", cls: "text-slate-900" },
  missing: { label: "Missing", cls: "text-orange-800" },
  pending: { label: "Not yet", cls: "text-slate-600" },
  quiet: { label: "No work", cls: "text-slate-400" },
  future: { label: "—", cls: "text-slate-300" },
};

/** The review sections, in the order a site day is told. */
const REVIEW: { key: string; label: string; from: string[] }[] = [
  { key: "work", label: "Work done", from: ["tasks"] },
  { key: "crew", label: "Crew on site", from: ["manpower"] },
  { key: "dv", label: "Deliveries and visitors", from: ["deliveries", "visitors"] },
  { key: "delays", label: "Delays", from: ["delays"] },
  { key: "safety", label: "Safety", from: ["safety"] },
  { key: "conditions", label: "Weather and ground", from: ["conditions"] },
  { key: "notes", label: "Other notes", from: ["notes"] },
];

/** `iso` moved by `days`, as a plain date (no timezone drift). */
const shift = (iso: string, days: number) => {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return d.toLocaleDateString("en-CA");
};

const weekday = (iso: string) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "short" });
const longDate = (iso: string) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
const time = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "still on";

/** `unsaid`: the write-up recorded this section as not mentioned, so an empty
 *  list is blank, never "None." (rollout test 2026-10-14). */
function show(v: unknown, unsaid = false): string {
  if (v == null || v === "") return "";
  if (typeof v === "string") return v;
  if (Array.isArray(v)) {
    if (!v.length) return unsaid ? "" : "None.";
    return v
      .map((row) =>
        row && typeof row === "object"
          ? Object.entries(row as Record<string, unknown>)
              .filter(([k, x]) => x !== null && x !== "" && x !== undefined && !["photos", "from_schedule", "ad_hoc", "removed"].includes(k))
              .map(([k, x]) => `${k.replace(/_/g, " ")}: ${x}`)
              .join(" · ")
          : String(row),
      )
      .join("\n");
  }
  if (typeof v === "object")
    return Object.entries(v as Record<string, unknown>)
      .filter(([, x]) => x !== null && x !== "" && x !== undefined)
      .map(([k, x]) => `${k.replace(/_/g, " ")}: ${x}`)
      .join("\n");
  return String(v);
}

export default function SitePage({ params }: { params: Promise<{ id: string }> }) {
  const { id: projectId } = use(params);
  const [view, setView] = useState<"reports" | "hours" | "photos">("reports");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex overflow-hidden rounded-md border border-slate-900 text-[13px]" role="group" aria-label="Site view">
          {(
            [
              ["reports", "Daily reports"],
              ["hours", "Hours"],
              ["photos", "Photos"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              type="button"
              onClick={() => setView(k)}
              className={`px-3 py-1.5 ${view === k ? "bg-slate-900 text-white" : ""}`}
              aria-pressed={view === k}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="text-[13px] text-slate-600">The crew files from the phone. This is the office view of the same reports.</span>
      </div>
      {view === "reports" && <Reports projectId={projectId} />}
      {view === "reports" && <AllReports projectId={projectId} />}
      {view === "hours" && <ProjectHours />}
      {view === "photos" && <Photos projectId={projectId} />}
    </div>
  );
}

function Reports({ projectId }: { projectId: string }) {
  const [day, setDay] = useState<string | undefined>(undefined);
  const [data, setData] = useState<SiteDay | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [transcript, setTranscript] = useState("");
  const [photoIds, setPhotoIds] = useState<string[]>([]);
  const [job, setJob] = useState<CaptureJob | null>(null);
  const [proposal, setProposal] = useState<CaptureProposal | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(() => {
    setErr(null);
    getSiteDay(projectId, day).then(setData).catch(setErr);
  }, [projectId, day]);
  useEffect(load, [load]);

  useEffect(() => {
    setTranscript("");
    setPhotoIds([]);
    setJob(null);
    setProposal(null);
    setSaved(null);
  }, [day]);

  useEffect(() => {
    if (!job || job.status === "done" || job.status === "error") {
      if (poll.current) clearInterval(poll.current);
      poll.current = null;
      if (job?.status === "done" && job.output?.proposal) setProposal(job.output.proposal);
      return;
    }
    poll.current = setInterval(() => getCapture(job.id).then(setJob).catch(setErr), 2000);
    return () => {
      if (poll.current) clearInterval(poll.current);
      poll.current = null;
    };
  }, [job]);

  if (err && !data) return <WorkspaceError error={err} onRetry={load} />;
  if (!data) return <p className="text-sm text-slate-500">Loading the day…</p>;

  const report = data.report;
  const filed = report?.status === "submitted" || report?.status === "finalized";
  const isToday = data.date === data.today;

  const writeUp = async () => {
    if (!transcript.trim()) return;
    setBusy(true);
    setErr(null);
    setProposal(null);
    setSaved(null);
    try {
      const tasks = data.scheduled.map((a) => ({
        activity_code: a.activity_code,
        name: a.name,
        is_critical: a.is_critical,
        status: "not_started",
      }));
      setJob(await createCapture(projectId, data.date, tasks as never, transcript));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!proposal) return;
    setBusy(true);
    setErr(null);
    try {
      // The report row is made here, on save — not by looking at the day.
      const r = await syncDailyReport(projectId, data.date);
      await updateDailyReport(r.id, { payload: mergeCapture(r.payload ?? ({} as DailyReportPayload), proposal, photoIds) });
      setProposal(null);
      setJob(null);
      setTranscript("");
      setPhotoIds([]);
      setSaved("Saved as a draft. Submit it when the day is complete.");
      load();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (!report) return;
    setBusy(true);
    setErr(null);
    try {
      await submitDailyReport(report.id);
      load();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const addPhoto = async (file: File) => {
    setBusy(true);
    setErr(null);
    try {
      const doc = await uploadDocument(projectId, file, "photo", undefined, true);
      setPhotoIds((p) => [...p, doc.id]);
      setTranscript((t) => `${t.trimEnd()}\n[photo: ${doc.filename}]\n`);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const nothingToReport = () =>
    setProposal((p) =>
      p
        ? {
            ...p,
            payload: { ...p.payload, safety: { incident: false, notes: "Nothing to report" } },
            not_recorded: p.not_recorded.filter((s) => s !== "safety"),
          }
        : p,
    );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <button type="button" onClick={() => setDay(shift(data.date, -7))}
          className="rounded border border-slate-300 bg-white px-2.5 py-1">
          ← Earlier week
        </button>
        <button type="button" disabled={data.date >= data.today}
          onClick={() => setDay(shift(data.date, 7) > data.today ? data.today : shift(data.date, 7))}
          className="rounded border border-slate-300 bg-white px-2.5 py-1 disabled:opacity-40">
          Later week →
        </button>
        <label className="flex items-center gap-1.5 text-slate-600">
          Go to
          <input type="date" value={data.date} max={data.today}
            onChange={(e) => e.target.value && setDay(e.target.value)}
            className="rounded border border-slate-300 px-1.5 py-0.5" />
        </label>
        {data.date !== data.today && (
          <button type="button" onClick={() => setDay(undefined)} className="text-blue-700">
            Back to today
          </button>
        )}
      </div>
      <div className="flex gap-1.5 overflow-x-auto pb-1">
        {data.week.map((d) => (
          <button
            key={d.date}
            type="button"
            onClick={() => setDay(d.date)}
            disabled={d.status === "future"}
            className={`min-w-[64px] flex-1 rounded-md border bg-white px-1 py-2 text-center text-xs ${
              d.date === data.date ? "border-2 border-slate-900" : "border-slate-300"
            }`}
          >
            <div>{d.date === data.today ? "Today" : weekday(d.date)}</div>
            <b className={STATUS[d.status].cls}>{STATUS[d.status].label}</b>
          </button>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
        <Box className="space-y-4 p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Label>
              {isToday ? "Today · " : ""}
              {longDate(data.date)}
            </Label>
            <span className="rounded border border-slate-400 px-1.5 text-xs">
              {report ? (filed ? "Filed" : "Draft") : "No report yet"}
            </span>
          </div>

          <div className="rounded-md border border-slate-300 p-3 text-[13px]">
            <b>On the schedule {isToday ? "today" : "that day"}</b>
            {!data.has_schedule ? (
              <p className="m-0 mt-1 text-slate-600">This project has no schedule, so nothing is expected. Anything described is recorded as work done.</p>
            ) : data.scheduled.length === 0 ? (
              <p className="m-0 mt-1 text-slate-600">Nothing scheduled.</p>
            ) : (
              <ul className="m-0 mt-1 list-none space-y-0.5 p-0">
                {data.scheduled.map((a, i) => (
                  <li key={`${a.activity_code}-${i}`}>
                    {a.name}
                    {a.is_critical && <span className="ml-1.5 text-xs text-red-700">critical path</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {report && (
            <div className="space-y-2 rounded-md border border-slate-300 p-3 text-[13px]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <b>The report</b>
                <div className="flex flex-wrap gap-2">
                  <Link href={`/projects/${projectId}/daily-reports/${report.id}`} className="text-blue-700">
                    Open the full report
                  </Link>
                  <a href={dailyReportPdfUrl(report.id)} target="_blank" rel="noopener noreferrer" className="text-blue-700">
                    PDF
                  </a>
                  {!filed && (
                    <button type="button" onClick={submit} disabled={busy} className="text-blue-700 disabled:opacity-50">
                      Submit
                    </button>
                  )}
                </div>
              </div>
              {REVIEW.map((s) => {
                const unsaid = report.payload.not_recorded ?? [];
                const text = s.from
                  .map((k) => show((report.payload as Record<string, unknown>)[k], unsaid.includes(k)))
                  .filter(Boolean)
                  .join("\n");
                return text ? (
                  <div key={s.key}>
                    <span className="font-semibold">{s.label}: </span>
                    <span className="whitespace-pre-line text-slate-700">{text}</span>
                  </div>
                ) : null;
              })}
              {report.payload.not_recorded?.length ? (
                <div className="text-slate-600">
                  Not mentioned, so left blank (not the same as none): {report.payload.not_recorded.join(", ")}.
                </div>
              ) : null}
              {report.payload.raised_for_office?.length ? (
                <div className="text-orange-900">
                  <span className="font-semibold">Raised for the office: </span>
                  {report.payload.raised_for_office.map((f) => `${f.what} (${f.why})`).join("; ")}
                </div>
              ) : null}
            </div>
          )}

          {filed ? (
            <p className="m-0 text-[13px] text-slate-600">Filed. Changes go through the full report.</p>
          ) : (
            <div className="space-y-2">
              <label htmlFor="day-text" className="block text-[15px] font-semibold">
                {report ? "Anything to add?" : "How did the day go?"}
              </label>
              <textarea
                id="day-text"
                rows={4}
                value={transcript}
                onChange={(e) => setTranscript(e.target.value)}
                placeholder="Talk or type. e.g. Forms stripped on the east footing, concrete truck late 40 min, 4 on site."
                className="w-full rounded-md border border-slate-400 px-2.5 py-2 text-sm"
              />
              <div className="flex flex-wrap items-center gap-2">
                <VoiceInput value={transcript} onChange={setTranscript} label="Record" />
                <label className="cursor-pointer rounded-md border border-slate-900 px-3 py-1.5 text-sm">
                  + Photos
                  <input
                    type="file"
                    accept="image/*"
                    multiple
                    className="hidden"
                    disabled={busy}
                    onChange={async (e) => {
                      const files = Array.from(e.target.files ?? []);
                      e.target.value = "";
                      for (const f of files) await addPhoto(f);
                    }}
                  />
                </label>
                {photoIds.length > 0 && <span className="text-xs text-slate-600">{photoIds.length} attached</span>}
                <button
                  type="button"
                  onClick={writeUp}
                  disabled={busy || !transcript.trim()}
                  className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                >
                  {busy && !proposal ? "Working…" : "Write it up"}
                </button>
              </div>
            </div>
          )}

          {job && (job.status === "queued" || job.status === "running") && (
            <div className="space-y-1 text-[13px] text-slate-600">
              Reading what was said…
              <JobProgress steps={job.steps} progress={job.progress} />
            </div>
          )}
          {job?.status === "error" && (
            <p className="m-0 text-[13px] text-red-700">That didn&apos;t work, and nothing was saved. {job.error ?? ""}</p>
          )}

          {proposal && (
            <div className="space-y-2 rounded-md border border-orange-300 bg-orange-50 p-4 text-[13px]">
              <b className="text-sm">Check this before it&apos;s saved</b>
              {REVIEW.map((s) => {
                const text = s.from.map((k) => show((proposal.payload as Record<string, unknown>)[k])).filter(Boolean).join("\n");
                const unmentioned = s.from.every((k) => proposal.not_recorded.includes(k));
                if (s.key === "safety" && unmentioned)
                  return (
                    <div key={s.key} className="border-t border-orange-200 pt-2">
                      <b>Safety</b>
                      <div className="text-orange-800">
                        Not mentioned. Add a line above and write it up again, or{" "}
                        <button type="button" onClick={nothingToReport} className="text-blue-700 underline">
                          record &ldquo;nothing to report&rdquo;
                        </button>
                        .
                      </div>
                    </div>
                  );
                return text ? (
                  <div key={s.key} className="border-t border-orange-200 pt-2">
                    <b>{s.label}</b>
                    <div className="whitespace-pre-line text-slate-800">{text}</div>
                  </div>
                ) : null;
              })}
              {proposal.flagged_for_office.length > 0 && (
                <div className="border-t border-orange-200 pt-2">
                  <b>Raised for the office</b>
                  <ul className="m-0 list-disc pl-4">
                    {proposal.flagged_for_office.map((f, i) => (
                      <li key={i}>
                        {f.what}: {f.why}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {proposal.not_recorded.filter((s) => s !== "safety").length > 0 && (
                <div className="border-t border-orange-200 pt-2 text-slate-700">
                  Not mentioned, so left blank (not the same as &ldquo;none&rdquo;):{" "}
                  {proposal.not_recorded.filter((s) => s !== "safety").join(", ")}.
                </div>
              )}
              {proposal.unclear.length > 0 && (
                <div className="border-t border-orange-200 pt-2 text-slate-700">Couldn&apos;t tell: {proposal.unclear.join("; ")}</div>
              )}
              <div className="flex flex-wrap gap-2 pt-1">
                <button type="button" onClick={save} disabled={busy} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50">
                  {busy ? "Saving…" : "Save report"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setProposal(null);
                    setJob(null);
                  }}
                  className="rounded-md border border-slate-900 px-3 py-1.5 text-sm"
                >
                  Try again
                </button>
              </div>
              <p className="m-0 text-xs text-slate-600">Saved as a draft; nothing is submitted until someone presses Submit.</p>
            </div>
          )}
          {saved && <p className="m-0 text-[13px] text-emerald-800">{saved}</p>}
          {err != null && <WorkspaceError error={err} />}
        </Box>

        <div className="space-y-4">
          <Box className="space-y-1 p-4 text-[13px]">
            <Label>Coming up</Label>
            {data.next_milestone ? (
              <div className="text-sm font-semibold">
                {data.next_milestone.label} · {new Date(`${data.next_milestone.date}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}
              </div>
            ) : (
              <p className="m-0 text-slate-600">{data.has_schedule ? "No milestone ahead on the schedule." : "No schedule."}</p>
            )}
          </Box>

          <Box className="space-y-1 p-4 text-[13px]">
            <Label>Hours {isToday ? "today" : "that day"}</Label>
            {data.hours.length === 0 ? (
              <p className="m-0 text-slate-600">Nobody clocked in.</p>
            ) : (
              <ul className="m-0 list-none space-y-0.5 p-0">
                {data.hours.map((h) => (
                  <li key={h.id}>
                    {h.user ?? "Someone"} · {time(h.punch_in_at)}–{time(h.punch_out_at)}
                    {h.location_flag && <span className="text-orange-800"> · off-site</span>}
                  </li>
                ))}
              </ul>
            )}
            {data.offsite_count > 0 && <div className="text-orange-800">Clocked in off-site: {data.offsite_count}</div>}
          </Box>

          <Box className="space-y-2 p-4 text-[13px]">
            <Label>Photos {isToday ? "today" : "that day"}</Label>
            {data.photos.length === 0 ? (
              <p className="m-0 text-slate-600">None uploaded.</p>
            ) : (
              <div className="grid grid-cols-3 gap-1.5">
                {data.photos.slice(0, 9).map((p) => (
                  <PhotoThumb key={p.id} id={p.id} name={p.filename} />
                ))}
              </div>
            )}
            {data.photos.length > 9 && <div className="text-slate-600">and {data.photos.length - 9} more under Photos</div>}
          </Box>
        </div>
      </div>
    </div>
  );
}

const PAGE = 60;

/** Every report on the project, newest first: the history the week strip
 *  does not show (it replaced the old Daily reports list). */
function AllReports({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<DailyReport[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => {
    listDailyReports(projectId).then(setRows).catch(setErr);
  }, [projectId]);
  if (err) return <WorkspaceError error={err} />;
  if (!rows) return null;
  const sorted = [...rows].sort((a, b) => b.report_date.localeCompare(a.report_date));
  return (
    <details className="rounded-md border border-slate-300 bg-white p-4 text-sm">
      <summary className="cursor-pointer font-medium">All reports ({rows.length})</summary>
      {sorted.length === 0 ? (
        <p className="m-0 mt-2 text-slate-600">No daily reports on this project yet.</p>
      ) : (
        <ul className="m-0 mt-2 list-none divide-y divide-slate-200 p-0">
          {sorted.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 py-2">
              <Link href={`/projects/${projectId}/daily-reports/${r.id}`} className="text-blue-700">
                {longDate(r.report_date)}
              </Link>
              <span className="text-slate-600">
                {r.status === "draft" ? "Draft" : "Filed"}
                {r.task_count ? ` · ${r.completed_count ?? 0}/${r.task_count} tasks done` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

function Photos({ projectId }: { projectId: string }) {
  const [photos, setPhotos] = useState<DocumentRead[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [shown, setShown] = useState(PAGE);
  useEffect(() => {
    listDocuments(projectId)
      .then((ds) =>
        setPhotos(ds.filter((d) => d.category === "photo").sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))),
      )
      .catch(setErr);
  }, [projectId]);
  if (err) return <WorkspaceError error={err} />;
  if (!photos) return <p className="text-sm text-slate-500">Loading photos…</p>;
  if (!photos.length) return <Box className="p-6 text-sm text-slate-600">No site photos yet.</Box>;
  return (
    <Box className="space-y-3 p-4">
      <Label>
        {photos.length.toLocaleString()} photo{photos.length === 1 ? "" : "s"}, newest first
      </Label>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-8">
        {photos.slice(0, shown).map((p) => (
          <div key={p.id} className="space-y-0.5">
            <PhotoThumb id={p.id} name={p.filename} />
            <div className="truncate text-[11px] text-slate-500" title={p.filename}>
              {p.created_at ? new Date(p.created_at).toLocaleDateString() : ""}
            </div>
          </div>
        ))}
      </div>
      {photos.length > shown && (
        <button type="button" onClick={() => setShown((n) => n + PAGE)} className="text-sm text-blue-700">
          Show {Math.min(PAGE, photos.length - shown)} more
        </button>
      )}
    </Box>
  );
}
