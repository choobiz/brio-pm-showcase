/**
 * What the Schedule › Plan page says about a schedule version, given what was
 * actually recorded and the bookings. Pure: dates are ISO "YYYY-MM-DD" strings
 * and `today` is passed in, so the build check can pin it.
 *
 * Kept free of "@/..." imports so scripts/check-schedule-plan.mjs can load it.
 */

export type PlanActivity = {
  activity_code: string;
  name: string;
  phase: string;
  start_date: string | null;
  finish_date: string | null;
  is_critical: boolean;
  is_milestone: boolean;
  predecessor_codes?: string[] | null;
};

export type PlanActual = {
  actual_start: string | null;
  actual_finish: string | null;
  last_reported: string | null;
  status: string | null;
};

export type PlanBooking = {
  id: string;
  title: string;
  status: string;
  activity_code: string | null;
  book_by: string | null;
  need_by: string | null;
  booked_for: string | null;
  late: boolean;
};

const OPEN_BOOKING = new Set(["to_book", "requested"]);

/** Same words as the Bookings & letters page (lib/milestones STATUS_LABEL). */
export const BOOKING_LABEL: Record<string, string> = {
  to_book: "To book",
  requested: "Requested",
  booked: "Booked",
  received: "Documents in",
  done: "Done",
  not_needed: "Not needed",
};

export function addDays(iso: string, n: number): string {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / 86_400_000);
}

function finishedOnRecord(act: PlanActual | undefined): boolean {
  return !!act?.actual_finish || act?.status === "done";
}

/** Activities with no finish on record that must have finished anyway,
 *  because work depending on them has started. 1234: forming (A100) started
 *  16 Oct, so the forms delivery (A75) before it happened, recorded or not. */
export function assumedDone(
  activities: PlanActivity[],
  actuals: Record<string, PlanActual>,
): Set<string> {
  const preds = new Map(activities.map((a) => [a.activity_code, a.predecessor_codes ?? []]));
  const out = new Set<string>();
  const stack: string[] = [];
  for (const a of activities) {
    const act = actuals[a.activity_code];
    if (act?.actual_start || finishedOnRecord(act)) stack.push(...(preds.get(a.activity_code) ?? []));
  }
  while (stack.length) {
    const c = stack.pop() as string;
    if (out.has(c)) continue;
    if (!finishedOnRecord(actuals[c])) out.add(c);
    stack.push(...(preds.get(c) ?? []));
  }
  return out;
}

/** Latest date anything was recorded (site report or typed). */
export function lastRecorded(actuals: Record<string, PlanActual>): string | null {
  let best: string | null = null;
  for (const a of Object.values(actuals)) {
    for (const d of [a.last_reported, a.actual_start, a.actual_finish]) {
      if (d && (!best || d > best)) best = d;
    }
  }
  return best;
}

/** Work the plan says should have started or finished by today, with nothing
 *  on record to say it did. Milestones (inspections, gates) are left out: they
 *  are tracked as bookings, not on site reports. */
export function unrecorded(
  activities: PlanActivity[],
  actuals: Record<string, PlanActual>,
  today: string,
): { shouldHaveStarted: PlanActivity[]; shouldHaveFinished: PlanActivity[] } {
  const shouldHaveStarted: PlanActivity[] = [];
  const shouldHaveFinished: PlanActivity[] = [];
  const assumed = assumedDone(activities, actuals);
  for (const a of activities) {
    if (a.is_milestone) continue;
    const act = actuals[a.activity_code];
    if (finishedOnRecord(act) || assumed.has(a.activity_code)) continue;
    if (a.finish_date && a.finish_date < today && act?.actual_start) shouldHaveFinished.push(a);
    else if (a.start_date && a.start_date <= today && !act?.actual_start) shouldHaveStarted.push(a);
  }
  return { shouldHaveStarted, shouldHaveFinished };
}

/** The first critical activity, by planned start, that is not finished. */
export function nextCritical(
  activities: PlanActivity[],
  actuals: Record<string, PlanActual>,
): PlanActivity | null {
  const assumed = assumedDone(activities, actuals);
  return (
    [...activities]
      .filter((a) => a.is_critical && !a.is_milestone && !finishedOnRecord(actuals[a.activity_code])
        && !assumed.has(a.activity_code))
      .sort((x, y) => (x.start_date ?? "").localeCompare(y.start_date ?? ""))[0] ?? null
  );
}

/** The open booking with the earliest book-by date. */
export function bookNext(bookings: PlanBooking[]): PlanBooking | null {
  return (
    bookings
      .filter((b) => OPEN_BOOKING.has(b.status) && b.book_by)
      .sort((x, y) => (x.book_by as string).localeCompare(y.book_by as string))[0] ?? null
  );
}

export type WindowRow = { activity: PlanActivity; bookings: PlanBooking[]; overdue: boolean };

/** Activities planned to run in [today, today + days], plus unfinished ones
 *  that should already be under way, each with its bookings attached. */
export function nextWindow(
  activities: PlanActivity[],
  actuals: Record<string, PlanActual>,
  bookings: PlanBooking[],
  today: string,
  days = 14,
): WindowRow[] {
  const end = addDays(today, days);
  const byCode = new Map<string, PlanBooking[]>();
  for (const b of bookings) {
    if (!b.activity_code || b.status === "not_needed") continue;
    (byCode.get(b.activity_code) ?? byCode.set(b.activity_code, []).get(b.activity_code)!).push(b);
  }
  const rows: WindowRow[] = [];
  const assumed = assumedDone(activities, actuals);
  for (const a of activities) {
    if (!a.start_date || !a.finish_date) continue;
    if (finishedOnRecord(actuals[a.activity_code]) || assumed.has(a.activity_code)) continue;
    const inWindow = a.start_date <= end && a.finish_date >= today;
    const overdue = a.finish_date < today;
    if (inWindow || overdue) rows.push({ activity: a, bookings: byCode.get(a.activity_code) ?? [], overdue });
  }
  return rows.sort((x, y) => (x.activity.start_date as string).localeCompare(y.activity.start_date as string));
}

// ── Record what happened: one answer per activity ─────────────────────────

export type EntryStatus = "done" | "started" | "not_started";

export type Entry = {
  status: EntryStatus;
  start?: string;     // ISO date
  finish?: string;
  percent?: number;
  reason?: string;
};

/** Reasons offered for "Not started" (the approved Record sketch). */
export const NOT_STARTED_REASONS = [
  "Waiting on material",
  "Waiting on approval",
  "Weather",
  "Crew availability",
] as const;

/** Why this answer can't be saved yet, or null. `recordedStart` is the start
 *  already on record (site report or typed), which a finish must not precede. */
export function entryProblem(e: Entry, recordedStart: string | null, today: string): string | null {
  const start = e.start || recordedStart;
  if (e.status === "done" && !e.finish) return "Say when it finished.";
  if (e.status === "started" && !start) return "Say when it started.";
  if (e.status === "not_started" && !e.reason?.trim()) return "Pick a reason.";
  for (const d of [e.start, e.finish]) if (d && d > today) return "That date is in the future.";
  if (e.status === "done" && start && e.finish && e.finish < start) return `Finish is before the start (${start}).`;
  if (e.percent != null && (e.percent < 0 || e.percent > 100)) return "Percent must be 0 to 100.";
  return null;
}

/** The PUT body for one answer. Done with no start anywhere takes the finish
 *  as its start, so the re-plan always has both dates. */
export function entryBody(e: Entry, recordedStart: string | null): Record<string, unknown> {
  if (e.status === "done") {
    const body: Record<string, unknown> = { status: "done", actual_finish: e.finish, percent_complete: 100, reason: null };
    if (e.start) body.actual_start = e.start;
    else if (!recordedStart) body.actual_start = e.finish;
    return body;
  }
  if (e.status === "started") {
    const body: Record<string, unknown> = { status: "started", reason: null };
    if (e.start) body.actual_start = e.start;
    if (e.percent != null) body.percent_complete = e.percent;
    return body;
  }
  return { status: "not_started", reason: e.reason?.trim() };
}

function longDate(iso: string, withYear = false): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-CA", {
    weekday: "short", month: "short", day: "numeric", timeZone: "UTC", ...(withYear ? { year: "numeric" } : {}),
  });
}

/** A client-facing progress note from the answers just saved. It is only a
 *  draft: the office reads and sends it from Messages. Activity codes and
 *  percentages stay out; reasons are kept because the client asks "why". */
export function clientDraft(saved: { name: string; entry: Entry; recordedStart: string | null }[]): string {
  const lines = saved.map(({ name, entry: e, recordedStart }) => {
    if (e.status === "done") return `- ${name}: finished ${longDate(e.finish as string)}.`;
    if (e.status === "started") return `- ${name}: under way since ${longDate((e.start || recordedStart) as string)}.`;
    return `- ${name}: not started yet (${(e.reason ?? "").trim().replace(/\.$/, "").toLowerCase()}).`;
  });
  return ["Hi, a quick update on progress at the site:", "", ...lines, "", "We'll let you know if this changes the finish date."].join("\n");
}

/** Client-facing note for a saved schedule update. Only a draft: it waits in
 *  Messages until the office sends it. */
export function finishDraft(oldFinish: string, newFinish: string): string {
  const d = daysBetween(oldFinish, newFinish);
  const change = d === 0
    ? `We're still on track to finish ${longDate(newFinish, true)}.`
    : d > 0
      ? `We now expect to finish ${longDate(newFinish, true)}, ${d} day${d === 1 ? "" : "s"} later than the ${longDate(oldFinish, true)} we gave you.`
      : `We now expect to finish ${longDate(newFinish, true)}, ${-d} day${d === -1 ? "" : "s"} earlier than the ${longDate(oldFinish, true)} we gave you.`;
  return ["Hi, we've updated the schedule with progress on site.", "", change,
    "The updated dates are in your portal. Happy to walk you through what changed."].join("\n");
}
