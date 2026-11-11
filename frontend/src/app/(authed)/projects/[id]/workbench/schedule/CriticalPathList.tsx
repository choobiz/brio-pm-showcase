"use client";

import { useMemo, useState } from "react";

// Activity shape we render. Matches the schedule API's ActivityRow
// subset we need on the schedule page. FastAPI serializes Postgres
// `numeric` columns as strings — we coerce to Number in render.
type Activity = {
  id?: string;
  activity_code: string;
  name: string;
  phase: string;
  predecessor_codes?: string[];
  start_date: string | null;
  finish_date: string | null;
  expected_wd: number | string | null;
  total_float_wd: number | null;
  free_float_wd: number | null;
  is_critical: boolean;
  is_milestone: boolean;
};

const fmtDate = (s: string | null | undefined) => {
  if (!s) return "—";
  return new Date(s).toLocaleDateString("en-CA", {
    month: "short",
    day: "numeric",
    year: "2-digit",
  });
};

const fmtNum = (n: number | string | null | undefined, digits = 1) => {
  if (n == null || n === "") return "—";
  const v = typeof n === "number" ? n : Number(n);
  return Number.isFinite(v) ? v.toFixed(digits) : "—";
};

// Float bucket → row styling. CRITICAL (TF=0) = red, NEAR (TF 1-2) =
// amber, BUFFER (TF 3-5) = subtle yellow, FLEX (TF ≥ 6) = white.
type Bucket = "CRITICAL" | "NEAR" | "BUFFER" | "FLEX";
function bucketFor(tf: number | null): Bucket {
  if (tf == null) return "FLEX";
  if (tf <= 0) return "CRITICAL";
  if (tf <= 2) return "NEAR";
  if (tf <= 5) return "BUFFER";
  return "FLEX";
}

const BUCKET_STYLES: Record<
  Bucket,
  { row: string; pill: string; label: string }
> = {
  CRITICAL: {
    row: "bg-red-50 border-l-4 border-red-500",
    pill: "bg-red-600 text-white",
    label: "Critical",
  },
  NEAR: {
    row: "bg-amber-50 border-l-4 border-amber-500",
    pill: "bg-amber-500 text-white",
    label: "Near-critical",
  },
  BUFFER: {
    row: "bg-yellow-50/40 border-l-4 border-yellow-300",
    pill: "bg-yellow-200 text-yellow-900",
    label: "Buffer",
  },
  FLEX: {
    row: "bg-white border-l-4 border-transparent",
    pill: "bg-slate-200 text-slate-700",
    label: "Flex",
  },
};

export function CriticalPathList({
  activities,
  criticalCodes,
}: {
  activities: Activity[];
  criticalCodes: string[];
}) {
  const cpSet = useMemo(() => new Set(criticalCodes), [criticalCodes]);

  // Filters
  const [phaseFilter, setPhaseFilter] = useState<string>("all");
  const [bucketFilter, setBucketFilter] = useState<"all" | Bucket>("all");
  const [helpOpen, setHelpOpen] = useState(false);

  const phases = useMemo(() => {
    const seen: string[] = [];
    const set = new Set<string>();
    for (const a of activities) {
      if (!set.has(a.phase)) {
        set.add(a.phase);
        seen.push(a.phase);
      }
    }
    return seen;
  }, [activities]);

  // Sort by total float ASC, then by phase order, then by activity code.
  // `null` floats sink to the bottom — they're typically un-computed rows.
  const sorted = useMemo(() => {
    const phaseOrder = new Map<string, number>(phases.map((p, i) => [p, i]));
    return [...activities].sort((a, b) => {
      const af = a.total_float_wd;
      const bf = b.total_float_wd;
      const aRank = af == null ? Number.POSITIVE_INFINITY : af;
      const bRank = bf == null ? Number.POSITIVE_INFINITY : bf;
      if (aRank !== bRank) return aRank - bRank;
      const ap = phaseOrder.get(a.phase) ?? 999;
      const bp = phaseOrder.get(b.phase) ?? 999;
      if (ap !== bp) return ap - bp;
      return a.activity_code.localeCompare(b.activity_code);
    });
  }, [activities, phases]);

  const rows = useMemo(() => {
    return sorted.filter((a) => {
      if (phaseFilter !== "all" && a.phase !== phaseFilter) return false;
      if (bucketFilter !== "all" && bucketFor(a.total_float_wd) !== bucketFilter)
        return false;
      return true;
    });
  }, [sorted, phaseFilter, bucketFilter]);

  // Counts per bucket (over the unfiltered sorted set)
  const counts = useMemo(() => {
    const c: Record<Bucket, number> = {
      CRITICAL: 0,
      NEAR: 0,
      BUFFER: 0,
      FLEX: 0,
    };
    for (const a of sorted) c[bucketFor(a.total_float_wd)]++;
    return c;
  }, [sorted]);

  return (
    <div className="border border-slate-200 bg-white">
      {/* Toolbar: counts + filters */}
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 px-3 py-2 text-xs">
        <div className="flex items-center gap-1">
          {(["CRITICAL", "NEAR", "BUFFER", "FLEX"] as Bucket[]).map((b) => {
            const s = BUCKET_STYLES[b];
            const active = bucketFilter === b;
            return (
              <button
                key={b}
                type="button"
                onClick={() => setBucketFilter(active ? "all" : b)}
                className={`px-2 py-1 rounded text-xs font-medium border ${
                  active
                    ? s.pill + " border-transparent"
                    : "bg-white text-slate-700 border-slate-300 hover:bg-slate-50"
                }`}
                title={`${s.label} — ${counts[b]} activities`}
              >
                {s.label} ({counts[b]})
              </button>
            );
          })}
          {bucketFilter !== "all" && (
            <button
              type="button"
              onClick={() => setBucketFilter("all")}
              className="ml-1 px-2 py-1 rounded text-xs text-slate-500 hover:text-slate-700"
            >
              Clear
            </button>
          )}
        </div>
        <div className="flex items-center gap-2 ml-auto">
          <label className="text-slate-500">Phase:</label>
          <select
            value={phaseFilter}
            onChange={(e) => setPhaseFilter(e.target.value)}
            className="text-xs border border-slate-300 rounded px-2 py-1"
          >
            <option value="all">All phases</option>
            {phases.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <span className="text-slate-400">
            {rows.length} / {sorted.length} shown
          </span>
          <button
            type="button"
            onClick={() => setHelpOpen((v) => !v)}
            className="px-2 py-1 rounded border border-slate-300 bg-white text-xs font-medium text-slate-700 hover:bg-slate-50"
            aria-expanded={helpOpen}
          >
            {helpOpen ? "Hide help" : "How to read this"}
          </button>
        </div>
      </div>

      {helpOpen && (
        <div className="border-b border-slate-200 bg-blue-50/40 px-4 py-3 text-xs text-slate-700 space-y-3">
          <div>
            <div className="font-semibold text-slate-800 mb-1">
              What you&rsquo;re looking at
            </div>
            <p>
              Every activity in the schedule, sorted by{" "}
              <strong>Total Float</strong> ascending — the activities with
              the least slack appear at the top. The critical path is the
              red band, because those rows have zero float.
            </p>
          </div>
          <div>
            <div className="font-semibold text-slate-800 mb-1">
              Float &amp; the four bands
            </div>
            <ul className="list-disc list-inside space-y-0.5">
              <li>
                <strong>Total Float</strong> = how many working days an
                activity can slip <em>without delaying the project finish</em>.
              </li>
              <li>
                <strong>Free Float</strong> = how many working days it can
                slip <em>without delaying the next activity</em>.
              </li>
            </ul>
            <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
              <span>
                <span className="inline-block w-3 h-3 rounded-sm bg-red-500 mr-1.5 align-middle" />
                <strong>Critical</strong> (TF = 0) — any delay slips the
                project
              </span>
              <span>
                <span className="inline-block w-3 h-3 rounded-sm bg-amber-500 mr-1.5 align-middle" />
                <strong>Near-critical</strong> (TF 1-2) — almost no room,
                watch closely
              </span>
              <span>
                <span className="inline-block w-3 h-3 rounded-sm bg-yellow-300 mr-1.5 align-middle" />
                <strong>Buffer</strong> (TF 3-5) — modest breathing room
              </span>
              <span>
                <span className="inline-block w-3 h-3 rounded-sm bg-slate-200 mr-1.5 align-middle" />
                <strong>Flex</strong> (TF ≥ 6) — plenty of slack
              </span>
            </div>
          </div>
          <div>
            <div className="font-semibold text-slate-800 mb-1">Controls</div>
            <ul className="list-disc list-inside space-y-0.5">
              <li>
                Click a <strong>Critical / Near / Buffer / Flex</strong>{" "}
                badge in the toolbar to filter to just that band — click
                again to unfilter.
              </li>
              <li>
                Use the <strong>Phase</strong> dropdown to narrow to a
                single phase of work.
              </li>
              <li>
                Rows tagged <strong>Milestone</strong> represent fixed
                project events rather than durations.
              </li>
            </ul>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-left">
            <tr>
              <th className="px-3 py-2 font-semibold text-slate-700">Status</th>
              <th className="px-3 py-2 font-semibold text-slate-700">Code</th>
              <th className="px-3 py-2 font-semibold text-slate-700">Activity</th>
              <th className="px-3 py-2 font-semibold text-slate-700">Phase</th>
              <th className="px-3 py-2 font-semibold text-slate-700 whitespace-nowrap">
                Start
              </th>
              <th className="px-3 py-2 font-semibold text-slate-700 whitespace-nowrap">
                Finish
              </th>
              <th className="px-3 py-2 font-semibold text-slate-700 text-right">
                Dur (wd)
              </th>
              <th className="px-3 py-2 font-semibold text-slate-700 text-right">
                Total Float
              </th>
              <th className="px-3 py-2 font-semibold text-slate-700 text-right">
                Free Float
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const bucket = bucketFor(a.total_float_wd);
              const style = BUCKET_STYLES[bucket];
              const crit =
                bucket === "CRITICAL" ||
                a.is_critical ||
                cpSet.has(a.activity_code);
              return (
                <tr
                  key={a.id ?? a.activity_code}
                  className={`${style.row} border-b border-slate-100 align-top`}
                >
                  <td className="px-3 py-2">
                    <span
                      className={`inline-block px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide ${style.pill}`}
                    >
                      {style.label}
                    </span>
                    {a.is_milestone && (
                      <span className="ml-1 inline-block px-1.5 py-0.5 rounded bg-slate-700 text-white text-[10px] font-bold uppercase">
                        Milestone
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-[11px] text-slate-700">
                    {a.activity_code}
                  </td>
                  <td className="px-3 py-2 text-slate-800">{a.name}</td>
                  <td className="px-3 py-2 text-slate-600 whitespace-nowrap">
                    {a.phase}
                  </td>
                  <td className="px-3 py-2 text-slate-600 whitespace-nowrap">
                    {fmtDate(a.start_date)}
                  </td>
                  <td className="px-3 py-2 text-slate-600 whitespace-nowrap">
                    {fmtDate(a.finish_date)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                    {fmtNum(a.expected_wd, 1)}
                  </td>
                  <td
                    className={`px-3 py-2 text-right tabular-nums font-semibold ${
                      crit ? "text-red-700" : "text-slate-700"
                    }`}
                  >
                    {a.total_float_wd == null ? "—" : a.total_float_wd}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                    {a.free_float_wd == null ? "—" : a.free_float_wd}
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={9}
                  className="px-3 py-6 text-center text-sm text-slate-400 italic"
                >
                  No activities match the current filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
