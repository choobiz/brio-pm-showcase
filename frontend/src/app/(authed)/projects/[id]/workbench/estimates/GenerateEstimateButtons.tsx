"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { JobProgress } from "@/components/JobProgress";
import { useRouter } from "next/navigation";

import {
  createAIJob,
  getAIJob,
  listAIJobs,
  streamAIJob,
  upTo,
  type AIJobRead,
  type AIJobStreamEvent,
} from "@/lib/aiJobs";
import { useJobCost } from "@/lib/useJobCost";

/**
 * Estimate generation. The primary action AUTO-BRANCHES: if the project has CAD
 * drawings it runs a measured takeoff and prices from it; otherwise it prices
 * from scope + rates — and records which path it took as provenance. A secondary
 * "scope-only" override forces rate pricing. Each fires an `estimate_full` job
 * that adds a NEW inactive draft — the active estimate is never touched.
 *
 * A run takes minutes, so how it REPORTS matters as much as what it does:
 *
 *   * POLLING is the source of truth, not the SSE stream. EventSource fires
 *     `error` on ordinary reconnects, and treating that as fatal abandoned a
 *     healthy job — the run continued on the server for another six minutes
 *     while the page said "Lost connection to the generation stream."
 *   * The elapsed clock runs from the job's own `created_at`, so it is right
 *     even after a reload, and it is the evidence that something is happening
 *     during the long stretch where the job reports no intermediate status.
 *   * On mount the component asks the server whether a run is already in
 *     progress and re-attaches. Closing the tab does not cancel the work, so the
 *     UI must not be the only place that knows about it.
 */

import type { EstimateReadiness } from "@/lib/workbench";
import DisciplinePlan from "./DisciplinePlan";

//: Poll cadence. Every tick is one billable serverless invocation through
//: `/api/backend/[...path]`, so the cadence is chosen per situation rather than
//: fixed. 3s is right for the first minute, when a fast failure is plausible
//: and the user is watching; after that the run reports nothing for minutes at
//: a time and SSE delivers the finish event anyway, so polling that hard only
//: re-confirms "still running" 200 times.
const POLL_MS = 3000;
const POLL_SETTLED_MS = 10000;
//: A background tab shows nobody anything. It still polls, because finishing
//: fires a Notification and that is the whole point of leaving the tab — but at
//: a tenth the rate.
const POLL_HIDDEN_MS = 30000;
const SETTLE_AFTER_MS = 60000;
//: ⚠️ The poller's only stop condition used to be a terminal job status, so a
//: job wedged in `running` polled at 3s forever — one forgotten tab was ~1,200
//: invocations an hour, indefinitely. Measured runs are 220-452s; 30 minutes is
//: roughly four times the longest we have seen, so anything past it is the
//: wedged case, which is exactly the one that must not poll forever.
const GIVE_UP_MS = 30 * 60 * 1000;
const KIND = "estimate_full";
//: Measured, not guessed: the 1234 run took 7m44s end to end. Saying "2-4
//: minutes" when it takes eight is how a working job comes to look stuck.
const TYPICAL_MINUTES = "5-10 minutes";
//: Used only to draw the bar. Labelled as an expectation, never as knowledge:
//: the job reports no percentage, so a bar that claimed to be one would lie.
const EXPECTED_SECONDS = 8 * 60;

function secondsSince(sinceIso: string | null): number {
  if (!sinceIso) return 0;
  return Math.max(0, (Date.now() - new Date(sinceIso).getTime()) / 1000);
}

function elapsed(sinceIso: string | null): string {
  if (!sinceIso) return "";
  const secs = Math.max(0, Math.floor((Date.now() - new Date(sinceIso).getTime()) / 1000));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

export function GenerateEstimateButtons({
  projectId,
  scopeId,
  disciplinePlan,
}: {
  projectId: string;
  scopeId: string | null;
  disciplinePlan?: EstimateReadiness["discipline_plan"];
}) {
  const router = useRouter();
  const cost = useJobCost(projectId, KIND);
  const [method, setMethod] = useState<null | "auto" | "scope">(null);
  const [job, setJob] = useState<AIJobRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  // null until the recommendation lands; then the user's choice.
  const [disciplines, setDisciplines] = useState<string[] | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [streamDown, setStreamDown] = useState(false);
  const [done, setDone] = useState(false);
  //: Set when we stop watching a run that has outlived every measured one.
  //: The job is not cancelled — we simply stop asking, and say so.
  const [gaveUp, setGaveUp] = useState(false);

  const timers = useRef<{
    poll?: number;
    tick?: number;
    es?: EventSource;
    onVis?: () => void;
  }>({});
  //: Bumped by every stop(), so a poll already in flight when we tear down
  //: cannot re-arm a timer behind us.
  const generation = useRef(0);
  const running = job?.status === "queued" || job?.status === "running";

  const stop = useCallback(() => {
    generation.current += 1;
    const t = timers.current;
    if (t.poll) window.clearTimeout(t.poll);
    if (t.tick) window.clearInterval(t.tick);
    if (t.onVis) document.removeEventListener("visibilitychange", t.onVis);
    t.es?.close();
    timers.current = {};
  }, []);

  /** Tell the person it finished even if they are on another tab.
   *  Asked for once, never nagged: a denied permission is never re-requested. */
  const notify = useCallback((title: string, body: string) => {
    try {
      if (typeof Notification === "undefined") return;
      if (Notification.permission === "granted") {
        new Notification(title, { body, tag: "brio-estimate" });
      }
    } catch {
      /* notifications are a courtesy, never a requirement */
    }
  }, []);

  const finish = useCallback(
    (j: AIJobRead) => {
      stop();
      setJob(j);
      setMethod(null);
      if (j.status === "error") {
        setError(j.error ?? "Generation failed");
        notify("Estimate failed", j.error ?? "The run did not complete.");
      } else {
        notify("Estimate ready", "A new draft has been added to this project.");
        setDone(true);
        router.refresh();
      }
    },
    [router, stop, notify],
  );

  /** Watch a job to completion. Polling drives it; SSE only makes it quicker. */
  const watch = useCallback(
    (jobId: string) => {
      stop();
      setStreamDown(false);
      setGaveUp(false);

      const gen = ++generation.current;
      const alive = () => generation.current === gen;
      const watchingSince = Date.now();

      /** Resolves true once the job is terminal and nothing further is needed. */
      const poll = async (): Promise<boolean> => {
        try {
          const j = await getAIJob(jobId);
          setJob(j);
          if (j.status === "done" || j.status === "error") {
            finish(j);
            return true;
          }
        } catch {
          // A failed poll is not a failed job. Keep polling; the clock keeps
          // running so the user can see it has not silently stalled.
          setStreamDown(true);
        }
        return false;
      };

      function nextDelay(): number {
        if (document.visibilityState !== "visible") return POLL_HIDDEN_MS;
        return Date.now() - watchingSince < SETTLE_AFTER_MS
          ? POLL_MS
          : POLL_SETTLED_MS;
      }

      function schedule() {
        if (!alive()) return;
        timers.current.poll = window.setTimeout(loop, nextDelay());
      }

      async function loop() {
        if (!alive()) return;
        if (Date.now() - watchingSince > GIVE_UP_MS) {
          stop();
          setGaveUp(true);
          return;
        }
        const terminal = await poll();
        if (terminal || !alive()) return;
        schedule();
      }

      const startTick = () => {
        if (timers.current.tick === undefined) {
          timers.current.tick = window.setInterval(() => setNow(Date.now()), 1000);
        }
      };

      // Coming back to the tab should show the truth at once, not the state as
      // of up to thirty seconds ago.
      const onVis = () => {
        if (!alive()) return;
        if (document.visibilityState === "visible") {
          setNow(Date.now());
          startTick();
          if (timers.current.poll) window.clearTimeout(timers.current.poll);
          void loop();
        } else if (timers.current.tick !== undefined) {
          window.clearInterval(timers.current.tick);
          timers.current.tick = undefined;
        }
      };
      timers.current.onVis = onVis;
      document.addEventListener("visibilitychange", onVis);

      void loop();
      if (document.visibilityState === "visible") startTick();

      try {
        const es = streamAIJob(jobId);
        timers.current.es = es;
        es.addEventListener("status", (ev) => {
          try {
            const p = JSON.parse((ev as MessageEvent).data) as AIJobStreamEvent;
            setStreamDown(false);
            if (p.status === "done" || p.status === "error") void poll();
          } catch {
            /* ignore malformed SSE frames */
          }
        });
        es.addEventListener("error", () => {
          // EventSource retries on its own, and this fires for every retry. The
          // job is unaffected either way — polling continues to decide.
          setStreamDown(true);
        });
      } catch {
        setStreamDown(true);
      }
    },
    [finish, stop],
  );

  // Re-attach to a run already in progress: the work outlives this page.
  useEffect(() => {
    let live = true;
    listAIJobs(projectId)
      .then((jobs) => {
        const active = jobs.find(
          (j) => j.kind === KIND && (j.status === "running" || j.status === "queued"),
        );
        if (live && active) {
          setJob(active);
          setMethod("auto");
          watch(active.id);
        }
      })
      .catch(() => {
        /* nothing in progress that we can see; the buttons stay available */
      });
    return () => {
      live = false;
      stop();
    };
  }, [projectId, watch, stop]);

  const run = (m: "auto" | "scope") => {
    if (!scopeId) {
      setError("No scope of work found — generate a scope first.");
      return;
    }
    setMethod(m);
    setError(null);
    setJob(null);
    setDone(false);
    setGaveUp(false);
    // Ask once, at the moment it becomes useful — a run this long is exactly
    // when someone switches tabs.
    try {
      if (typeof Notification !== "undefined" && Notification.permission === "default") {
        void Notification.requestPermission();
      }
    } catch {
      /* ignore */
    }
    createAIJob({
      project_id: projectId,
      kind: KIND,
      input: {
        scope_id: scopeId,
        method: m,
        detail_level: "fully_itemized",
        // Pressing the button approved this amount; the run stops before going over it.
        approve_usd: cost ?? undefined,
        // Omitted entirely when nothing was chosen, so the worker keeps its own
        // six-discipline default rather than receiving an empty list.
        ...(disciplines && disciplines.length ? { disciplines } : {}),
      },
    })
      .then((j) => {
        setJob(j);
        watch(j.id);
      })
      .catch((e) => {
        setMethod(null);
        setError(String(e));
      });
  };

  const since = job?.started_at ?? job?.created_at ?? null;
  void now; // the 1s tick re-renders so `elapsed` stays current

  return (
    <div className="space-y-2">
      <DisciplinePlan
        plan={disciplinePlan}
        value={disciplines}
        onChange={setDisciplines}
        disabled={!!job && job.status !== "done" && job.status !== "error"}
      />
      <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={running || method !== null || cost === null}
        onClick={() => run("auto")}
        className="flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        style={{ background: "var(--color-brio-primary)" }}
        title="Auto: runs a CAD takeoff if drawings allow, otherwise prices from scope + rates"
      >
        {running || method === "auto" ? <Spinner /> : null}
        {running || method === "auto"
          ? "Generating…"
          : cost === null
            ? "Working out the cost…"
            : `Generate Estimate · ${upTo(cost)}`}
      </button>
      <button
        type="button"
        disabled={running || method !== null || cost === null}
        onClick={() => run("scope")}
        className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        title="Force rate-based pricing from the scope, skipping the CAD takeoff"
      >
        Scope-only (rates){cost === null ? "" : ` · ${upTo(cost)}`}
      </button>

      {!running && !done && !error && (
        <span className="text-xs text-slate-500">
          Takes about {TYPICAL_MINUTES} · runs in the background
        </span>
      )}
      </div>

      {running && gaveUp && (
        <div className="mt-2 w-full border border-amber-300 bg-amber-50 px-3 py-2">
          <p className="text-sm text-amber-900">
            <b>Still running after 30 minutes.</b> That is far longer than any
            run we have measured, so this page has stopped checking rather than
            poll indefinitely. The job has <b>not</b> been cancelled — it may
            still finish on its own.
          </p>
          <button
            type="button"
            onClick={() => job && watch(job.id)}
            className="mt-1.5 text-xs font-medium text-amber-800 underline hover:no-underline"
          >
            Check again
          </button>
        </div>
      )}

      {running && !gaveUp && (
        <div className="mt-2 w-full border border-slate-200 bg-white p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <span className="text-sm font-medium text-slate-800">
              Generating estimate
              <span className="ml-2 font-normal text-slate-500">
                {method === "scope"
                  ? "pricing from scope + rates"
                  : "measured takeoff, then pricing"}
              </span>
            </span>
            <span className="text-sm tabular-nums text-slate-700">
              <b>{elapsed(since)}</b>
              <span className="text-slate-400"> of ~{TYPICAL_MINUTES}</span>
            </span>
          </div>

          {/* The job reports no percentage, so this tracks elapsed time against
              what a run usually costs and stops short of full — a bar that hit
              100% and kept going would be worse than no bar. */}
          <div className="mt-2 h-1.5 w-full overflow-hidden bg-slate-100">
            <div
              className="h-full bg-blue-500 transition-[width] duration-1000"
              style={{ width: `${Math.min(92, (secondsSince(since) / EXPECTED_SECONDS) * 100).toFixed(1)}%` }}
            />
          </div>

          {job?.steps?.length ? (
            <JobProgress steps={job.steps} progress={job.progress} className="mt-2" />
          ) : null}

          <p className="mt-2 text-xs text-slate-500">
            You can leave this page — the run continues on the server and you will
            get a notification when it finishes. It adds a{" "}
            <b className="font-medium text-slate-700">new draft</b> and never
            changes the active estimate.
            {streamDown && (
              <span className="ml-1 text-slate-400">
                (live updates unavailable — still checking periodically)
              </span>
            )}
          </p>
        </div>
      )}

      {done && !error && (
        <div className="mt-2 flex w-full items-center justify-between gap-3 border border-emerald-300 bg-emerald-50 px-3 py-2">
          <span className="text-sm text-emerald-800">
            <b>Estimate ready.</b> Added as a new draft in the table below — review
            it, then keep it if you want it to be the one downstream
            documents use.
            {typeof job?.output?.estimate_version_id === "string" && (
              <a
                href={`/projects/${projectId}/estimate/${job.output.estimate_version_id}`}
                className="ml-1 font-medium underline"
              >
                Open it
              </a>
            )}
          </span>
          <button
            type="button"
            onClick={() => setDone(false)}
            className="shrink-0 text-xs text-emerald-700 underline hover:no-underline"
          >
            dismiss
          </button>
        </div>
      )}

      {error && (
        <div className="mt-2 w-full border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          <b>Generation failed.</b> {error}
        </div>
      )}
    </div>
  );
}

function Spinner() {
  return (
    <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" aria-hidden="true">
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
        fill="none"
      />
      <path
        className="opacity-90"
        fill="currentColor"
        d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z"
      />
    </svg>
  );
}
