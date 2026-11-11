// CPM + PERT recompute for project schedules. Pure TypeScript, no I/O.
// Takes an activity list, returns a new array with ES/EF/LS/LF/float/critical
// + (when project start is provided) start/finish dates.

export type CpmActivity = {
  activity_code: string;
  name: string;
  phase: string;
  predecessor_codes: string[];
  lag_wd: number;
  optimistic_wd: number | null;
  most_likely_wd: number | null;
  pessimistic_wd: number | null;
  expected_wd: number | null;
  variance: number | null;
  early_start_wd?: number | null;
  early_finish_wd?: number | null;
  late_start_wd?: number | null;
  late_finish_wd?: number | null;
  total_float_wd?: number | null;
  free_float_wd?: number | null;
  start_date?: string | null;
  finish_date?: string | null;
  is_critical?: boolean;
  is_milestone?: boolean;
};

/** Kahn's algorithm — topological order respecting predecessor_codes. */
function topoSort(activities: CpmActivity[]): string[] {
  const inDeg = new Map<string, number>();
  const adj = new Map<string, string[]>();
  for (const a of activities) {
    inDeg.set(a.activity_code, 0);
    adj.set(a.activity_code, []);
  }
  for (const a of activities) {
    for (const pred of a.predecessor_codes || []) {
      if (adj.has(pred)) {
        adj.get(pred)!.push(a.activity_code);
        inDeg.set(a.activity_code, (inDeg.get(a.activity_code) ?? 0) + 1);
      }
    }
  }
  const queue: string[] = [];
  for (const [code, d] of inDeg) if (d === 0) queue.push(code);
  const out: string[] = [];
  while (queue.length) {
    const code = queue.shift()!;
    out.push(code);
    for (const s of adj.get(code) ?? []) {
      inDeg.set(s, inDeg.get(s)! - 1);
      if (inDeg.get(s) === 0) queue.push(s);
    }
  }
  return out;
}

/** PERT expected duration + variance from three-point estimate. */
function pertify(a: CpmActivity): CpmActivity {
  const o = Number(a.optimistic_wd ?? a.most_likely_wd ?? a.expected_wd ?? 0);
  const m = Number(a.most_likely_wd ?? a.expected_wd ?? 0);
  const p = Number(a.pessimistic_wd ?? a.most_likely_wd ?? a.expected_wd ?? 0);
  const expected = (o + 4 * m + p) / 6;
  const variance = Math.pow((p - o) / 6, 2);
  return {
    ...a,
    expected_wd: Math.round(expected * 100) / 100,
    variance: Math.round(variance * 100) / 100,
  };
}

/** Working-day to calendar date — skip Sat/Sun + supplied stat holidays. */
export function wdToDate(
  projectStartIso: string,
  wd: number,
  holidaysIso: Set<string>,
): string {
  // wd = 0 → project start date (work begins on day 0)
  const d = new Date(projectStartIso + "T00:00:00Z");
  let counted = 0;
  while (counted < wd) {
    d.setUTCDate(d.getUTCDate() + 1);
    const dow = d.getUTCDay();
    const iso = d.toISOString().slice(0, 10);
    if (dow !== 0 && dow !== 6 && !holidaysIso.has(iso)) counted++;
  }
  return d.toISOString().slice(0, 10);
}

export type CpmRollup = {
  activities: CpmActivity[];
  deterministicFinishWd: number;
  deterministicFinishDate: string | null;
  p50FinishDate: string | null;
  p80FinishDate: string | null;
  criticalPathCodes: string[];
  projectStdDevWd: number;
};

/**
 * Full CPM + PERT pass. Inputs are activities (with three-point durations
 * + predecessor_codes + lag_wd) + project start + holidays. Returns a new
 * activity array with ES/EF/LS/LF/float/critical + start/finish dates,
 * plus project-level rollups (deterministic + P50 + P80 finish).
 */
export function recomputeSchedule(
  inputActivities: CpmActivity[],
  projectStartIso: string | null,
  holidaysIso: string[] = [],
): CpmRollup {
  const holidays = new Set(holidaysIso);

  // 1. PERT each activity
  const activities = inputActivities.map(pertify);
  const byCode = new Map(activities.map((a) => [a.activity_code, a]));

  // 2. Topological order
  const order = topoSort(activities);

  // 3. Forward pass: ES, EF
  for (const code of order) {
    const a = byCode.get(code)!;
    let es = 0;
    for (const predCode of a.predecessor_codes || []) {
      const pred = byCode.get(predCode);
      if (pred?.early_finish_wd != null) {
        const cand = pred.early_finish_wd + (Number(a.lag_wd) || 0);
        if (cand > es) es = cand;
      }
    }
    a.early_start_wd = Math.round(es * 100) / 100;
    a.early_finish_wd = Math.round((es + (a.expected_wd || 0)) * 100) / 100;
  }

  const deterministicFinishWd = Math.max(
    ...activities.map((a) => a.early_finish_wd ?? 0),
  );

  // 4. Backward pass: LF, LS, float, critical
  for (const code of [...order].reverse()) {
    const a = byCode.get(code)!;
    const successors = activities.filter((b) =>
      (b.predecessor_codes || []).includes(a.activity_code),
    );
    let lf = deterministicFinishWd;
    for (const s of successors) {
      if (s.late_start_wd != null) {
        const cand = s.late_start_wd - (Number(s.lag_wd) || 0);
        if (cand < lf) lf = cand;
      }
    }
    a.late_finish_wd = Math.round(lf * 100) / 100;
    a.late_start_wd = Math.round((lf - (a.expected_wd || 0)) * 100) / 100;
    a.total_float_wd = Math.round((a.late_start_wd - (a.early_start_wd || 0)) * 100) / 100;
    a.is_critical = Math.abs(a.total_float_wd) < 0.01;
  }

  // 5. Convert ES/EF to calendar dates
  if (projectStartIso) {
    for (const a of activities) {
      a.start_date = wdToDate(projectStartIso, a.early_start_wd ?? 0, holidays);
      a.finish_date = wdToDate(projectStartIso, a.early_finish_wd ?? 0, holidays);
    }
  }

  // 6. PERT rollup — project std-dev = sqrt(sum of variances on critical path)
  const critical = activities.filter((a) => a.is_critical);
  const totalVariance = critical.reduce((s, a) => s + (a.variance || 0), 0);
  const projectStdDevWd = Math.sqrt(totalVariance);

  // P50 = deterministic (using expected durations). P80 = deterministic + ~0.84 σ,
  // floored at +8% so the buffer is always visible — independent-activity CLT
  // under-states correlated construction risk. Mirrors the authoritative Python
  // engine (app/services/cpm.py) so an editor re-save never shrinks P80.
  const p50Wd = deterministicFinishWd;
  const p80Wd = Math.max(p50Wd + 0.84 * projectStdDevWd, p50Wd * 1.08);

  return {
    activities,
    deterministicFinishWd,
    deterministicFinishDate: projectStartIso
      ? wdToDate(projectStartIso, deterministicFinishWd, holidays)
      : null,
    p50FinishDate: projectStartIso
      ? wdToDate(projectStartIso, Math.ceil(p50Wd), holidays)
      : null,
    p80FinishDate: projectStartIso
      ? wdToDate(projectStartIso, Math.ceil(p80Wd), holidays)
      : null,
    criticalPathCodes: critical.map((a) => a.activity_code),
    projectStdDevWd: Math.round(projectStdDevWd * 100) / 100,
  };
}
