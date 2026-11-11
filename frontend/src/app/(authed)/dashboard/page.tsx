"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Box, Label, Tag } from "@/components/kit";
import { WorkspaceError } from "@/components/WorkspaceError";
import { fmtMoney } from "@/lib/fmt";
import { dayLabel, getHome, type ActiveJob, type Home } from "@/lib/home";
import { STATUS_LABELS } from "@/lib/projects";
import type { RegisterItem } from "@/lib/register";

/**
 * Home — what needs you, across every job (wireframe round 1, Home board).
 * Replaces the old Dashboard and the preview workspace's "Needs attention".
 *
 * Order is the point: the things waiting on you first, then the jobs, then the
 * week, then what is merely worth knowing. Recent activity is not here yet:
 * the activity records are split across two tables with no reader (plan, P1).
 */
export default function HomePage() {
  const [home, setHome] = useState<Home | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setError(null);
    getHome().then(setHome).catch(setError);
  }, []);
  useEffect(load, [load]);

  if (error) return <WorkspaceError error={error} onRetry={load} className="max-w-3xl" />;
  if (!home) return <p className="text-sm text-slate-500">Loading…</p>;

  const needs = home.needs_you.register.length + home.needs_you.client.length;

  return (
    <div className="max-w-6xl space-y-5">
      <h1 className="m-0 text-2xl font-bold text-slate-900">Home</h1>

      <Box>
        <div className="mb-3 flex items-baseline justify-between gap-3">
          <Label>Needs you · {needs}</Label>
          <span className="text-xs text-slate-500">Across every job</span>
        </div>
        {needs === 0 ? (
          <p className="m-0 text-sm text-slate-600">Nothing is waiting on you.</p>
        ) : (
          <ul className="m-0 list-none divide-y divide-slate-200 p-0">
            {home.needs_you.register.map((i) => (
              <RegisterRow key={i.key} item={i} />
            ))}
            {home.needs_you.client.map((a, n) => (
              <li key={`${a.type}-${a.project_id}-${n}`} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-slate-900">{a.label}</div>
                  <div className="text-[13px] text-slate-600">
                    {a.project_name} · {a.detail}
                  </div>
                </div>
                <Link href={a.href} className="text-sm text-blue-700 hover:text-blue-900">
                  Open →
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Box>

      <section>
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <Label>Active jobs · {home.active_jobs.length}</Label>
          <span className="text-xs text-slate-500">{home.active_rule}</span>
        </div>
        {home.active_jobs.length === 0 ? (
          <Box>
            <p className="m-0 text-sm text-slate-600">No job is under way or has been worked on lately.</p>
          </Box>
        ) : (
          <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(260px,1fr))]">
            {home.active_jobs.map((j) => (
              <JobCard key={j.project_id} job={j} />
            ))}
          </div>
        )}
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <Box>
          <Label className="mb-3">Next 7 days</Label>
          {home.this_week.length === 0 ? (
            <p className="m-0 text-sm text-slate-600">Nothing on any schedule in the next 7 days.</p>
          ) : (
            <ul className="m-0 list-none space-y-2 p-0">
              {home.this_week.map((w, n) => (
                <li key={n} className="flex gap-3 text-sm">
                  <span className="w-24 shrink-0 text-slate-500">{dayLabel(w.date)}</span>
                  <span className="min-w-0">
                    <span className="font-medium text-slate-900">{w.name}</span>{" "}
                    <span className="text-slate-500">
                      {w.event === "milestone" ? "· milestone" : w.event}
                    </span>
                    <br />
                    <Link href={`/projects/${w.project_id}/workbench/schedule`} className="text-[13px] text-blue-700">
                      {w.project_name}
                    </Link>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Box>

        <Box>
          <Label className="mb-3">Worth a look</Label>
          {home.worth_a_look.length === 0 ? (
            <p className="m-0 text-sm text-slate-600">Nothing.</p>
          ) : (
            <ul className="m-0 list-none divide-y divide-slate-200 p-0">
              {home.worth_a_look.map((i) => (
                <RegisterRow key={i.key} item={i} />
              ))}
            </ul>
          )}
        </Box>
      </div>
    </div>
  );
}

function RegisterRow({ item }: { item: RegisterItem }) {
  const [why, setWhy] = useState(false);
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-slate-900">
            {item.title}
            {item.count != null && item.count > 1 && <span className="font-normal text-slate-500"> · {item.count.toLocaleString()}</span>}
          </div>
          <div className="text-[13px] text-slate-600">
            {item.project_name && <>{item.project_name} · </>}
            {item.summary}{" "}
            <button
              type="button"
              onClick={() => setWhy((v) => !v)}
              className="text-blue-700 underline-offset-2 hover:underline"
            >
              {why ? "less" : "why"}
            </button>
          </div>
        </div>
        <div className="flex gap-3">
          {item.actions.map((a) => (
            <Link key={a.href} href={a.href} className="text-sm text-blue-700 hover:text-blue-900">
              {a.label} →
            </Link>
          ))}
        </div>
      </div>
      {why && <p className="m-0 mt-1.5 max-w-3xl text-[13px] text-slate-600">{item.why}</p>}
    </li>
  );
}

function JobCard({ job }: { job: ActiveJob }) {
  const money = job.contract;
  return (
    <Link
      href={`/projects/${job.project_id}/overview`}
      className="flex flex-col gap-2 rounded-lg border border-slate-300 bg-white p-4 text-slate-900 no-underline hover:border-slate-500"
    >
      <div className="flex items-start justify-between gap-2">
        <b className="text-[15px] leading-snug">{job.name}</b>
        <Tag tone={job.why_active === "status" ? "strong" : "plain"}>
          {STATUS_LABELS[job.status as keyof typeof STATUS_LABELS] ?? job.status}
        </Tag>
      </div>
      <div className="text-[13px] text-slate-600">
        {job.next_milestone ? (
          <>
            Next: <b className="text-slate-900">{job.next_milestone.label}</b> · {dayLabel(job.next_milestone.date)}
          </>
        ) : (
          "No upcoming milestone on a schedule"
        )}
      </div>
      <div className="mt-auto text-[13px] text-slate-600">
        {money ? (
          <>
            <b className="text-base text-slate-900">{fmtMoney(money.amount)}</b> + GST ·{" "}
            {money.source === "contract" ? "contract" : `from estimate v${money.version_number}`}
          </>
        ) : (
          "No estimate yet"
        )}
      </div>
      {job.why_active === "recent_work" && job.last_activity_at && (
        <div className="text-xs text-slate-500">
          Worked on {new Date(job.last_activity_at).toLocaleDateString("en-CA", { month: "short", day: "numeric" })}
        </div>
      )}
    </Link>
  );
}
