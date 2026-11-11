"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import { Card } from "@/components/ui/card";
import { WorkspaceError } from "@/components/WorkspaceError";
import { getDivisions, type DivisionSet, type DivisionsResponse } from "@/lib/divisions";
import { listProjects, type ProjectRead } from "@/lib/projects";

/**
 * The cost divisions an estimate is organised into.
 *
 * Until this page the only way to read a division set was to open a migration —
 * which made "store them as data so they can be changed without a deploy"
 * half-true at best.
 *
 * Read-only, and the page says so. Changing a set changes the shape of every
 * future estimate on every project that maps to it, which is a wider blast
 * radius than anything else here, so editing goes with the other company-wide
 * changes rather than in a preview.
 */

const TYPE_WORDS: Record<string, string> = {
  residential_new: "Residential — new build",
  residential_reno: "Residential — renovation",
  commercial_ti: "Commercial — tenant improvement",
  commercial_new: "Commercial — new build",
};

export default function DivisionsPage() {
  const [data, setData] = useState<DivisionsResponse | null>(null);
  const [projects, setProjects] = useState<ProjectRead[]>([]);
  const [projectId, setProjectId] = useState<string>("");
  // Carried from the project page so the project is chosen ONCE. Reaching
  // this page directly still falls back to the picker below.
  const preselected = useSearchParams().get("project");
  useEffect(() => { if (preselected) setProjectId(preselected); }, [preselected]);
  const [err, setErr] = useState<unknown>(null);
  // Re-runs the fetch on retry rather than only clearing the message.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    listProjects().then(setProjects).catch(() => setProjects([]));
  }, []);
  useEffect(() => {
    setErr(null);
    getDivisions(projectId || undefined).then(setData).catch(setErr);
  }, [projectId, attempt]);

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-5">
        <h1 className="text-2xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>
          Cost breakdown
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          How an estimate is broken down, and which breakdown a project uses.
        </p>
      </header>

      {err != null && (
        <WorkspaceError
          error={err}
          onRetry={() => setAttempt((n) => n + 1)}
          className="mb-4"
        />
      )}

      <Card className="mb-5 p-4">
        <label className="text-sm text-slate-600">
          Which breakdown would a project use?
          <select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            className="ml-2 min-w-[20rem] rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">— pick a project —</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        {data?.resolved && (
          <p
            className={`mt-3 rounded px-3 py-2 text-sm ${
              data.resolved.set_code
                ? "bg-emerald-50 text-emerald-900"
                : "bg-amber-50 text-amber-900"
            }`}
          >
            {data.resolved.why}
          </p>
        )}
      </Card>

      {data?.sets.map((s) => (
        <SetCard key={s.id} set={s} highlight={data.resolved?.set_code === s.code} />
      ))}

      {data && (
        <p className="mt-6 border-t border-slate-200 pt-3 text-xs leading-relaxed text-slate-500">
          {data.editable_note}
        </p>
      )}
    </div>
  );
}

function SetCard({ set: s, highlight }: { set: DivisionSet; highlight?: boolean }) {
  const [showAll, setShowAll] = useState(false);
  // CSI records all 50 numbers; showing the 15 reserved ones by default would
  // bury the 21 that are actually used.
  const shown = showAll ? s.divisions : s.divisions.filter((d) => d.used_by_default);

  return (
    <Card className={`mb-4 p-4 ${highlight ? "border-l-4 border-l-emerald-500" : ""}`}>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h2 className="text-[15px] font-semibold">{s.name}</h2>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-600">
          {s.standard}
        </span>
        {s.version && <span className="text-xs text-slate-500">{s.version}</span>}
        <span className="ml-auto text-xs text-slate-500">
          {s.project_types.map((t) => TYPE_WORDS[t] ?? t).join(" · ") || "not mapped to a type"}
        </span>
      </div>

      <p className="mt-1 text-xs text-slate-500">
        {s.counts.used_by_default} used by default
        {s.counts.usable !== s.counts.used_by_default && (
          <> · {s.counts.usable - s.counts.used_by_default} available but off</>
        )}
        {s.counts.reserved > 0 && <> · {s.counts.reserved} reserved by the standard</>}
      </p>
      {s.notes && <p className="mt-2 text-xs leading-relaxed text-slate-600">{s.notes}</p>}

      <ol className="mt-3 space-y-0.5">
        {shown.map((d) => (
          <li
            key={d.number}
            className={`flex items-baseline gap-2 border-b border-slate-100 py-1 text-sm ${
              d.reserved ? "text-slate-400" : ""
            }`}
          >
            <span className="w-8 shrink-0 font-mono text-xs text-slate-500">{d.number}</span>
            <span className="min-w-0 flex-1">{d.title}</span>
            {!d.reserved && (
              <span
                className="shrink-0 text-xs text-slate-400"
                title="Trade contingency added to this division"
              >
                {d.contingency_pct === 0 ? "no contingency" : `+${d.contingency_pct}%`}
              </span>
            )}
            {!d.used_by_default && !d.reserved && (
              <span className="shrink-0 rounded border border-slate-300 px-1 text-[10px] text-slate-500">
                off
              </span>
            )}
          </li>
        ))}
      </ol>

      {s.divisions.length !== shown.length && (
        <button
          onClick={() => setShowAll((v) => !v)}
          className="mt-2 text-xs text-slate-600 underline-offset-2 hover:underline"
        >
          {showAll
            ? "Show only what is used"
            : `Show all ${s.counts.total}, including ${s.counts.reserved} reserved`}
        </button>
      )}
    </Card>
  );
}
