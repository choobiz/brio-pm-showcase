"use client";

import { Card } from "@/components/ui/card";

/**
 * What a long job is doing, while it is doing it.
 *
 * ⚠️ WHY THIS IS WORTH A COMPONENT. `estimate_full` averages 375 seconds and
 * peaks at 456. For that whole time the only thing on screen was the word
 * "running", which is indistinguishable from "hung" — which is why people
 * reload the page and start a second one.
 *
 * ⚠️ NO PERCENTAGE WHEN THERE ARE NO STEPS. 0% implies a job that has not
 * started; a job that never reports steps is a different thing, and the server
 * returns `percent: null` for exactly that reason. Rendering "0%" there would
 * invent a state.
 *
 * A failed step shows as failed and the run continues — that is the real
 * behaviour, and the step list is the only place that says a trade is missing
 * from the result.
 */

export interface JobStep {
  name: string;
  status: "pending" | "running" | "done" | "error";
  started_at?: string | null;
  finished_at?: string | null;
  note?: string | null;
}

export interface JobProgressSummary {
  total: number;
  done: number;
  current: string | null;
  failed: string[];
  /** null when the job reports no steps at all — not the same as 0. */
  percent: number | null;
}

const TONE: Record<string, string> = {
  done: "text-emerald-700",
  running: "text-slate-900 font-medium",
  error: "text-red-700",
  pending: "text-slate-400",
};

const MARK: Record<string, string> = {
  done: "✓", running: "▸", error: "✕", pending: "·",
};

export function JobProgress({
  steps,
  progress,
  className = "",
}: {
  steps?: JobStep[] | null;
  progress?: JobProgressSummary | null;
  className?: string;
}) {
  const rows = steps ?? [];
  // A job with no steps renders nothing rather than an empty frame — most
  // kinds finish in under a minute and never report any.
  if (!rows.length) return null;

  const p = progress ?? null;

  return (
    <Card className={`p-3 ${className}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          {p?.current ? `Working on ${p.current.replace(/_/g, " ")}` : "Steps"}
        </span>
        <span className="font-mono text-xs tabular-nums text-slate-600">
          {p ? `${p.done} of ${p.total}` : `${rows.length} steps`}
          {p?.percent != null ? ` · ${p.percent}%` : ""}
        </span>
      </div>

      {p?.percent != null && (
        <div className="mt-2 h-1 w-full overflow-hidden rounded bg-slate-200">
          <div
            className="h-full bg-slate-800 transition-[width] duration-500"
            style={{ width: `${p.percent}%` }}
          />
        </div>
      )}

      <ul className="mt-2 space-y-0.5">
        {rows.map((s) => (
          <li key={s.name} className={`text-xs ${TONE[s.status] ?? ""}`}>
            <span className="inline-block w-4" aria-hidden>{MARK[s.status] ?? "·"}</span>
            {s.name.replace(/_/g, " ")}
            {s.note ? <span className="text-slate-500"> — {s.note}</span> : null}
          </li>
        ))}
      </ul>

      {p?.failed?.length ? (
        <p className="mt-2 text-xs text-red-700">
          {p.failed.join(", ")} failed. The run continued, so the result is missing that part.
        </p>
      ) : null}
    </Card>
  );
}
