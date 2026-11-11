"use client";

import { useState } from "react";

import { CriticalPathList } from "./CriticalPathList";
import { GanttChart } from "./GanttChart";
import type { ActivityActual } from "@/lib/workbench";
import { NetworkDiagram } from "./NetworkDiagram";

// All-in-one client wrapper that owns the active view state + renders
// the appropriate component. Keeps the schedule server page lean — it
// just fetches + serialises the activities/inspections payload.

type Activity = {
  id?: string;
  activity_code: string;
  name: string;
  phase: string;
  predecessor_codes?: string[];
  start_date: string | null;
  finish_date: string | null;
  expected_wd: number | string | null;
  early_start_wd: number | string | null;
  early_finish_wd: number | string | null;
  late_start_wd: number | string | null;
  late_finish_wd: number | string | null;
  total_float_wd: number | null;
  free_float_wd: number | null;
  is_critical: boolean;
  is_milestone: boolean;
};

type Inspection = {
  id?: string;
  activity_id?: string | null;
  authority?: string;
  inspection_type?: string;
};

type View = "gantt" | "list" | "network";

const VIEWS: { id: View; label: string; hint: string }[] = [
  { id: "gantt", label: "Gantt", hint: "Timeline of activities" },
  { id: "list", label: "Critical Path List", hint: "Activities sorted by float" },
  { id: "network", label: "Network Diagram", hint: "Activity-on-Node precedence" },
];

export function ScheduleViews({
  activities,
  inspections,
  criticalCodes,
  projectStart,
  projectEnd,
  actuals = {},
}: {
  actuals?: Record<string, ActivityActual>;
  activities: Activity[];
  inspections: Inspection[];
  criticalCodes: string[];
  projectStart: string | null;
  projectEnd: string | null;
}) {
  const [view, setView] = useState<View>("gantt");

  return (
    <section>
      <div className="flex items-center gap-1 mb-3" role="tablist">
        {VIEWS.map((v) => {
          const active = v.id === view;
          return (
            <button
              key={v.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setView(v.id)}
              title={v.hint}
              className={`px-3 py-1.5 text-sm font-medium border-b-2 transition-colors ${
                active
                  ? "border-blue-600 text-blue-700"
                  : "border-transparent text-slate-600 hover:text-slate-900"
              }`}
            >
              {v.label}
            </button>
          );
        })}
      </div>

      {view === "gantt" && (
        <GanttChart
          activities={activities}
          inspections={inspections}
          projectStart={projectStart}
          projectEnd={projectEnd}
          criticalCodes={criticalCodes}
          actuals={actuals}
        />
      )}
      {view === "list" && (
        <CriticalPathList
          activities={activities}
          criticalCodes={criticalCodes}
        />
      )}
      {view === "network" && (
        <NetworkDiagram
          activities={activities}
          criticalCodes={criticalCodes}
        />
      )}
    </section>
  );
}
