"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { fmtDate } from "@/lib/fmt";
import { recomputeSchedule, CpmActivity } from "@/lib/cpm";
import { saveScheduleAction } from "./actions";

const numCls =
  "border border-slate-300 rounded px-1.5 py-0.5 text-xs w-16 text-right tabular-nums focus:outline-none focus:ring-1 focus:ring-blue-500";
const textCls =
  "border border-slate-300 rounded px-2 py-0.5 text-xs w-full focus:outline-none focus:ring-1 focus:ring-blue-500";

export function ScheduleEditor({
  projectId,
  scheduleId,
  initialActivities,
  initialInspections,
  projectStart,
  workingHours,
  holidays,
}: {
  projectId: string;
  scheduleId: string;
  initialActivities: CpmActivity[];
  initialInspections: {
    activity_code?: string | null;
    authority?: string;
    inspection_type?: string;
    lead_time_days?: number | null;
    gates?: string | null;
  }[];
  projectStart: string;
  workingHours: string;
  holidays: string[];
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [activities, setActivities] = useState<CpmActivity[]>(initialActivities);
  const [startDate, setStartDate] = useState<string>(projectStart);
  const [error, setError] = useState<string | null>(null);

  const startChanged = startDate !== projectStart;

  // Live recompute on every render — fast enough at PROJB's 30 activities.
  // Editing the start date re-bases every activity's calendar dates.
  const rollup = useMemo(
    () => recomputeSchedule(activities, startDate, holidays),
    [activities, startDate, holidays],
  );

  function editActivity(code: string, patch: Partial<CpmActivity>) {
    setActivities((prev) =>
      prev.map((a) => (a.activity_code === code ? { ...a, ...patch } : a)),
    );
  }

  // Append a fresh activity (M=1, no predecessors) with a generated unique code.
  function addActivity() {
    setActivities((prev) => [
      ...prev,
      {
        activity_code: nextCode(prev.map((a) => a.activity_code)),
        name: "New activity",
        phase: "",
        predecessor_codes: [],
        lag_wd: 0,
        optimistic_wd: null,
        most_likely_wd: 1,
        pessimistic_wd: null,
        expected_wd: null,
        variance: null,
        is_milestone: false,
      },
    ]);
  }

  // Remove an activity AND scrub its code from every other activity's
  // predecessor list, so no dangling references survive into recompute/save.
  function removeActivity(code: string) {
    setActivities((prev) =>
      prev
        .filter((a) => a.activity_code !== code)
        .map((a) => ({
          ...a,
          predecessor_codes: (a.predecessor_codes || []).filter((p) => p !== code),
        })),
    );
  }

  function onSave() {
    setError(null);
    startTransition(async () => {
      try {
        await saveScheduleAction(projectId, {
          parent_schedule_id: scheduleId,
          project_start: startDate,
          working_hours: workingHours,
          holidays,
          deterministic_finish_date: rollup.deterministicFinishDate,
          p50_finish_date: rollup.p50FinishDate,
          p80_finish_date: rollup.p80FinishDate,
          project_expected_wd: rollup.deterministicFinishWd,
          project_std_dev_wd: rollup.projectStdDevWd,
          critical_path_activity_codes: rollup.criticalPathCodes,
          notes: `Saved from in-browser CPM editor — ${rollup.criticalPathCodes.length} critical activities${startChanged ? `; start date → ${startDate}` : ""}`,
          activities: rollup.activities,
          inspections: initialInspections,
        });
        router.push(`/projects/${projectId}/workbench/schedule`);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Save failed");
      }
    });
  }

  function onCancel() {
    router.push(`/projects/${projectId}/workbench/schedule`);
  }

  return (
    <div className="space-y-4">
      <header className="sticky top-0 z-10 -mx-4 px-4 py-3 bg-white border-b border-slate-200 flex flex-wrap gap-3 items-end">
        <div className="flex-1 min-w-[260px]">
          <h1 className="text-xl font-semibold">Editing schedule</h1>
          <p className="text-sm text-slate-600 mt-0.5">
            Edit the start date, three-point durations, predecessors + lag.
            Recompute is live — save creates a new schedule version.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={pending}
            className="px-3 py-1.5 rounded border border-slate-300 text-sm hover:bg-slate-50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={pending}
            className="px-3 py-1.5 rounded bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {pending ? "Saving…" : "Save as new schedule version"}
          </button>
        </div>
        {error && (
          <div className="w-full text-sm text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1">
            {error}
          </div>
        )}
      </header>

      <section className="rounded border border-blue-200 bg-blue-50/40 p-3 grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
        <div>
          <div className="text-[10px] uppercase text-slate-500">Start</div>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="mt-0.5 border border-slate-300 rounded px-1.5 py-0.5 text-sm tabular-nums focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          {startChanged && (
            <div className="text-[10px] text-blue-700 mt-0.5">
              changed from {fmtDate(projectStart)} — all dates rebased
            </div>
          )}
        </div>
        <KV label="Deterministic" v={fmtDate(rollup.deterministicFinishDate)} />
        <KV label="PERT P50" v={fmtDate(rollup.p50FinishDate)} />
        <KV label="PERT P80" v={fmtDate(rollup.p80FinishDate)} />
        <KV
          label="Critical / Total"
          v={`${rollup.criticalPathCodes.length} / ${activities.length}`}
        />
      </section>

      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-500">
          {activities.length} activities · edit durations/predecessors inline, or
          add and remove rows — recompute is live.
        </span>
        <button
          type="button"
          onClick={addActivity}
          className="px-2.5 py-1 rounded border border-slate-300 text-xs font-medium hover:bg-slate-50"
        >
          + Add activity
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs border border-slate-200">
          <thead className="bg-slate-50 text-slate-700">
            <tr>
              <th className="px-2 py-1 text-left">ID</th>
              <th className="px-2 py-1 text-left">Name</th>
              <th className="px-2 py-1 text-left">Phase</th>
              <th className="px-2 py-1 text-left w-44">Predecessors</th>
              <th className="px-2 py-1 text-right w-16">Lag</th>
              <th className="px-2 py-1 text-right w-16">O</th>
              <th className="px-2 py-1 text-right w-16">M</th>
              <th className="px-2 py-1 text-right w-16">P</th>
              <th className="px-2 py-1 text-right w-16">Exp</th>
              <th className="px-2 py-1 whitespace-nowrap">Start</th>
              <th className="px-2 py-1 whitespace-nowrap">Finish</th>
              <th className="px-2 py-1 text-right w-12">TF</th>
              <th className="px-2 py-1 w-16">Status</th>
              <th className="px-2 py-1 w-8"></th>
            </tr>
          </thead>
          <tbody>
            {rollup.activities.map((a) => (
              <tr
                key={a.activity_code}
                className={`border-t border-slate-100 ${a.is_critical ? "bg-amber-50/40" : ""}`}
              >
                <td className="px-2 py-1 font-medium whitespace-nowrap">{a.activity_code}</td>
                <td className="px-2 py-1">
                  <input
                    type="text"
                    value={a.name}
                    onChange={(e) =>
                      editActivity(a.activity_code, { name: e.target.value })
                    }
                    className={textCls}
                    placeholder="Activity name"
                  />
                </td>
                <td className="px-2 py-1">
                  <input
                    type="text"
                    value={a.phase}
                    onChange={(e) =>
                      editActivity(a.activity_code, { phase: e.target.value })
                    }
                    className={`${textCls} w-24`}
                    placeholder="Phase"
                  />
                </td>
                <td className="px-2 py-1">
                  <input
                    type="text"
                    value={(a.predecessor_codes || []).join(",")}
                    onChange={(e) =>
                      editActivity(a.activity_code, {
                        predecessor_codes: e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      })
                    }
                    className={textCls}
                    placeholder="e.g., A01,A02"
                  />
                </td>
                <td className="px-2 py-1">
                  <input
                    type="number"
                    step="1"
                    value={a.lag_wd ?? 0}
                    onChange={(e) =>
                      editActivity(a.activity_code, { lag_wd: Number(e.target.value) })
                    }
                    className={numCls}
                  />
                </td>
                <td className="px-2 py-1">
                  <input
                    type="number"
                    step="0.5"
                    value={a.optimistic_wd ?? ""}
                    onChange={(e) =>
                      editActivity(a.activity_code, {
                        optimistic_wd: e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                    className={numCls}
                  />
                </td>
                <td className="px-2 py-1">
                  <input
                    type="number"
                    step="0.5"
                    value={a.most_likely_wd ?? ""}
                    onChange={(e) =>
                      editActivity(a.activity_code, {
                        most_likely_wd: e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                    className={numCls}
                  />
                </td>
                <td className="px-2 py-1">
                  <input
                    type="number"
                    step="0.5"
                    value={a.pessimistic_wd ?? ""}
                    onChange={(e) =>
                      editActivity(a.activity_code, {
                        pessimistic_wd: e.target.value === "" ? null : Number(e.target.value),
                      })
                    }
                    className={numCls}
                  />
                </td>
                <td className="px-2 py-1 text-right tabular-nums text-slate-500">
                  {a.expected_wd == null ? "" : Number(a.expected_wd).toFixed(2)}
                </td>
                <td className="px-2 py-1 whitespace-nowrap text-slate-600">
                  {fmtDate(a.start_date)}
                </td>
                <td className="px-2 py-1 whitespace-nowrap text-slate-600">
                  {fmtDate(a.finish_date)}
                </td>
                <td className="px-2 py-1 text-right tabular-nums">
                  {a.total_float_wd == null ? "" : Number(a.total_float_wd).toFixed(0)}
                </td>
                <td className="px-2 py-1">
                  {a.is_critical && (
                    <span className="inline-block px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-[10px] font-bold uppercase">
                      Critical
                    </span>
                  )}
                </td>
                <td className="px-2 py-1 text-center">
                  <button
                    type="button"
                    onClick={() => removeActivity(a.activity_code)}
                    title="Delete activity"
                    aria-label={`Delete ${a.activity_code}`}
                    className="text-slate-300 hover:text-red-600 text-sm leading-none"
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Generate a unique activity code — next integer beyond the highest numeric
 *  suffix in use, prefixed "A" (e.g., A01, A02…). Avoids clashing with existing
 *  codes so predecessor references stay unambiguous. */
function nextCode(existing: string[]): string {
  let max = 0;
  for (const c of existing) {
    const m = c.match(/(\d+)\s*$/);
    if (m) max = Math.max(max, parseInt(m[1], 10));
  }
  return `A${String(max + 1).padStart(2, "0")}`;
}

function KV({ label, v }: { label: string; v: string | null }) {
  return (
    <div>
      <div className="text-[10px] uppercase text-slate-500">{label}</div>
      <div className="text-base font-semibold tabular-nums">{v ?? "—"}</div>
    </div>
  );
}
