"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { getThumbnailUrl, uploadDocument } from "@/lib/documents";
import {
  type DailyReport,
  type DailyReportPayload,
  type DailyTask,
  type TaskStatus,
  submitDailyReport,
  syncDailyReport,
  updateDailyReport,
} from "@/lib/dailyReports";
import {
  cacheReport,
  flushOutbox,
  flushPhotos,
  getCachedByProjectDate,
  getPending,
  pendingCount,
  pendingPhotoCounts,
  queueMutation,
  queuePhoto,
} from "@/lib/fieldOffline";
import { getProject } from "@/lib/projects";

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const STATUS_BTNS: { s: TaskStatus; label: string; on: string }[] = [
  { s: "in_progress", label: "In progress", on: "bg-sky-600 text-white border-sky-600" },
  { s: "completed", label: "Complete", on: "bg-emerald-600 text-white border-emerald-600" },
  { s: "delayed", label: "Delayed", on: "bg-red-600 text-white border-red-600" },
  { s: "not_worked", label: "Not worked", on: "bg-slate-600 text-white border-slate-600" },
];

interface TaskWithPhotos extends DailyTask {
  photos?: string[];
}

export default function FieldReport() {
  const { id } = useParams<{ id: string }>();
  const [report, setReport] = useState<DailyReport | null>(null);
  const [p, setP] = useState<DailyReportPayload>({});
  const [projectName, setProjectName] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [online, setOnline] = useState(true);
  const [pending, setPending] = useState(0);
  const [photoPending, setPhotoPending] = useState<Record<string, number>>({});
  const photoInput = useRef<HTMLInputElement | null>(null);
  const photoTask = useRef<number | null>(null);
  const reportIdRef = useRef<string | null>(null);

  const refreshPending = useCallback(async () => {
    setPending(await pendingCount());
    if (reportIdRef.current) setPhotoPending(await pendingPhotoCounts(reportIdRef.current));
  }, []);

  const boot = useCallback(async () => {
    setError(null);
    getProject(id).then((pr) => setProjectName(pr.name)).catch(() => {});
    if (navigator.onLine) {
      try {
        await flushOutbox();
        await flushPhotos();
        const r = await syncDailyReport(id, today());
        await cacheReport(r);
        reportIdRef.current = r.id;
        setReport(r);
        setP(r.payload || {});
        setDirty(false);
        await refreshPending();
        return;
      } catch {
        /* fall through to the offline cache */
      }
    }
    const cached = await getCachedByProjectDate(id, today());
    if (cached) {
      reportIdRef.current = cached.id;
      setReport(cached);
      setP(cached.payload || {});
      setDirty(false);
    } else {
      setError(
        navigator.onLine
          ? "Couldn't load today's report."
          : "You're offline and today's report hasn't been downloaded yet. Connect once to sync it.",
      );
    }
    await refreshPending();
  }, [id, refreshPending]);

  useEffect(() => {
    boot();
  }, [boot]);

  useEffect(() => {
    setOnline(navigator.onLine);
    const on = () => {
      setOnline(true);
      flushOutbox()
        .then(() => flushPhotos())
        .then(refreshPending);
    };
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, [refreshPending]);

  const tasks = (p.tasks ?? []) as TaskWithPhotos[];
  const setTask = (i: number, patch: Partial<TaskWithPhotos>) => {
    const next = [...tasks];
    next[i] = { ...next[i], ...patch };
    setP((prev) => ({ ...prev, tasks: next }));
    setDirty(true);
  };

  // Local-first: write the cache + queue the mutation, then flush if online.
  const persist = async (payload: DailyReportPayload, submit = false) => {
    if (!report) return;
    const optimistic: DailyReport = {
      ...report,
      payload,
      status: submit ? "submitted" : report.status,
    };
    await cacheReport(optimistic);
    const prior = await getPending(report.id);
    await queueMutation({
      reportId: report.id,
      payload,
      submit: submit || prior?.submit,
      updatedAt: Date.now(),
    });
    setReport(optimistic);
    setDirty(false);
    if (navigator.onLine) await flushOutbox();
    await refreshPending();
  };

  const save = async () => {
    setBusy("save");
    setError(null);
    try {
      await persist(p, false);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };
  const submit = async () => {
    setBusy("submit");
    setError(null);
    try {
      await persist(p, true);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const pickPhoto = (i: number) => {
    photoTask.current = i;
    photoInput.current?.click();
  };
  const pickSitePhoto = () => {
    photoTask.current = -1; // report-level
    photoInput.current?.click();
  };
  const onPhoto = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    const i = photoTask.current;
    e.target.value = "";
    if (!files.length || i == null || !report) return;
    setBusy("photo");
    setError(null);
    try {
      const reportLevel = i === -1; // -1 = a report-level "Site Photo"
      if (navigator.onLine) {
        // Online: upload each + link into the current payload.
        const ids: string[] = [];
        for (const file of files) {
          const doc = await uploadDocument(id, file, "photo");
          ids.push(doc.id);
        }
        let nextPayload: DailyReportPayload;
        if (reportLevel) {
          nextPayload = { ...p, photos: [...(p.photos ?? []), ...ids] };
        } else {
          const nextTasks = [...tasks];
          nextTasks[i] = { ...nextTasks[i], photos: [...(tasks[i].photos ?? []), ...ids] };
          nextPayload = { ...p, tasks: nextTasks };
        }
        setP(nextPayload);
        await persist(nextPayload, false);
      } else {
        // Offline: queue each blob; they upload + link on reconnect.
        for (const file of files) {
          await queuePhoto({
            reportId: report.id,
            projectId: id,
            taskCode: reportLevel ? "__report__" : (tasks[i].activity_code ?? null),
            taskIndex: i,
            filename: file.name || `photo-${Date.now()}.jpg`,
            mime: file.type || "image/jpeg",
            blob: file,
            createdAt: Date.now(),
          });
        }
        await refreshPending();
      }
    } catch (err) {
      setError("Couldn't save the photo. " + String(err));
    } finally {
      setBusy(null);
    }
  };

  const photoBadge = (t: TaskWithPhotos, i: number): string => {
    const key = t.activity_code ?? `idx:${i}`;
    const server = t.photos?.length ?? 0;
    const queued = photoPending[key] ?? 0;
    if (server + queued === 0) return "";
    return queued > 0 ? ` · ${server + queued} (${queued} pending)` : ` · ${server}`;
  };

  if (!report && !error)
    return <div className="px-1 text-sm text-slate-500">Loading today&rsquo;s report…</div>;
  if (!report)
    return (
      <div className="space-y-3">
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">{error}</div>
        <button onClick={boot} className="w-full rounded-lg bg-[#0068A0] py-3 font-medium text-white">
          Retry
        </button>
      </div>
    );

  const finalized = report.status === "finalized";
  const syncLabel = !online
    ? pending > 0 ? `Offline · ${pending} queued` : "Offline"
    : pending > 0 ? `Syncing · ${pending}` : "Synced";
  const syncCls = !online
    ? "bg-amber-100 text-amber-800"
    : pending > 0 ? "bg-amber-100 text-amber-800" : "bg-emerald-100 text-emerald-700";

  return (
    <div className="space-y-3">
      {/* No `capture` → the OS offers Camera OR Photo Library; `multiple` allows several at once. */}
      <input ref={photoInput} type="file" accept="image/*" multiple onChange={onPhoto} className="hidden" />

      <div className="flex items-center justify-between px-1">
        <Link href={`/field/${id}`} className="text-xs text-slate-500">← Back</Link>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${syncCls}`}>{syncLabel}</span>
      </div>
      <div className="px-1">
        <h1 className="text-lg font-semibold text-slate-900">{projectName || "Daily Report"}</h1>
        <div className="text-xs text-slate-500">{report.report_date} · {report.status}</div>
      </div>

      {report.schedule_drifted && !finalized && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800">
          The schedule changed — reopen from the office view to re-sync this day&rsquo;s tasks.
        </div>
      )}
      {error && <div className="rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">{error}</div>}

      {tasks.length === 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-500">
          No scheduled tasks for today.
        </div>
      )}

      {tasks.map((t, i) => (
        <div key={i} className={`rounded-xl border bg-white p-3 shadow-sm ${t.removed ? "opacity-60" : "border-slate-200"}`}>
          <div className="flex items-start gap-2">
            {t.is_critical && <span className="mt-1 inline-block h-2.5 w-2.5 shrink-0 bg-[#0068A0]" title="Critical path" />}
            <div className="min-w-0 flex-1">
              <div className="font-medium leading-snug text-slate-900">{t.name}</div>
              <div className="text-[10px] uppercase tracking-wide text-slate-400">
                {t.phase}{t.ad_hoc ? " · added on site" : ""}{t.removed ? " · no longer scheduled" : ""}
              </div>
            </div>
          </div>

          <div className="mt-2 grid grid-cols-4 gap-1.5">
            {STATUS_BTNS.map((b) => (
              <button
                key={b.s}
                disabled={finalized}
                onClick={() => setTask(i, { status: b.s })}
                className={`rounded-lg border py-2 text-[11px] font-semibold ${t.status === b.s ? b.on : "border-slate-200 bg-white text-slate-600"}`}
              >
                {b.label}
              </button>
            ))}
          </div>

          {(t.status === "in_progress" || t.status === "completed") && (
            <div className="mt-2 flex items-center gap-2">
              <span className="text-xs text-slate-500">Progress</span>
              <button disabled={finalized} onClick={() => setTask(i, { percent_complete: Math.max(0, (t.percent_complete || 0) - 10) })} className="h-8 w-8 rounded-lg border border-slate-200 text-lg">−</button>
              <span className="w-12 text-center text-sm font-medium">{t.percent_complete || 0}%</span>
              <button disabled={finalized} onClick={() => setTask(i, { percent_complete: Math.min(100, (t.percent_complete || 0) + 10) })} className="h-8 w-8 rounded-lg border border-slate-200 text-lg">+</button>
            </div>
          )}

          <textarea
            disabled={finalized}
            value={t.notes ?? ""}
            onChange={(e) => setTask(i, { notes: e.target.value })}
            placeholder="Notes / blockers…"
            rows={2}
            className="mt-2 w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm"
          />
          <button
            disabled={finalized || busy === "photo"}
            onClick={() => pickPhoto(i)}
            className="mt-2 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 active:bg-slate-50"
          >
            {busy === "photo" ? "Saving…" : "📷 Photo"}
            {photoBadge(t, i)}
          </button>
          {(t.photos?.length ?? 0) > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {t.photos!.map((docId) => <PhotoThumb key={docId} docId={docId} />)}
            </div>
          )}
        </div>
      ))}

      <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
        <div className="flex items-center justify-between">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Site Photos</div>
          <button
            disabled={finalized || busy === "photo"}
            onClick={pickSitePhoto}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 active:bg-slate-50"
          >
            {busy === "photo" ? "Saving…" : "📷 Add photo"}
            {(() => {
              const server = p.photos?.length ?? 0;
              const queued = photoPending["__report__"] ?? 0;
              return server + queued > 0
                ? queued > 0 ? ` · ${server + queued} (${queued} pending)` : ` · ${server}`
                : "";
            })()}
          </button>
        </div>
        <p className="mt-1 text-[11px] text-slate-400">
          General site photos, not tied to a task — useful when there&rsquo;s no schedule.
        </p>
        {(p.photos?.length ?? 0) > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {p.photos!.map((docId) => <PhotoThumb key={docId} docId={docId} />)}
          </div>
        )}
        {(photoPending["__report__"] ?? 0) > 0 && (
          <div className="mt-1 text-[11px] text-amber-700">
            {photoPending["__report__"]} photo(s) queued — will upload when back online.
          </div>
        )}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
        <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">End-of-day notes</div>
        <textarea
          disabled={finalized}
          value={p.notes ?? ""}
          onChange={(e) => { setP((prev) => ({ ...prev, notes: e.target.value })); setDirty(true); }}
          rows={3}
          placeholder="Anything affecting upcoming days — delays, deliveries, decisions…"
          className="w-full rounded-lg border border-slate-200 px-2 py-1.5 text-sm"
        />
      </div>

      {busy === "photo" && (
        <div className="px-1 text-xs text-slate-500">Uploading photo(s)…</div>
      )}
      {!finalized && (
        <div
          className="fixed inset-x-0 bottom-0 z-10 flex gap-2 border-t border-slate-200 bg-white p-3"
          style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
        >
          <button
            onClick={save}
            disabled={busy !== null || (!dirty && pending === 0)}
            className="flex-1 rounded-lg border border-slate-300 py-3 font-medium text-slate-700 disabled:opacity-50"
          >
            {busy === "save" ? "Saving…" : dirty ? "Save" : online || pending === 0 ? "Saved" : "Save"}
          </button>
          <button
            onClick={submit}
            disabled={busy !== null}
            className="flex-1 rounded-lg bg-[#0068A0] py-3 font-medium text-white disabled:opacity-50"
          >
            {busy === "submit" ? "…" : online ? "Submit" : "Submit (queued)"}
          </button>
        </div>
      )}
    </div>
  );
}

/** Small thumbnail for an uploaded photo Document — confirms what's been added. */
function PhotoThumb({ docId }: { docId: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let ok = true;
    getThumbnailUrl(docId).then((r) => { if (ok) setUrl(r.thumbnail_url); }).catch(() => {});
    return () => { ok = false; };
  }, [docId]);
  return url ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt="site photo" className="h-14 w-14 rounded border border-slate-200 object-cover" />
  ) : (
    <div className="h-14 w-14 animate-pulse rounded bg-slate-100" />
  );
}
