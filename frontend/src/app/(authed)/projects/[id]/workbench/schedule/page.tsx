import Link from "next/link";
import {
  listSchedules,
  getSchedule,
  getActuals,
  listBookings,
  fmtDate,
  type ActivityActual,
  type BookingItem,
} from "@/lib/workbench";
import {
  BOOKING_LABEL,
  assumedDone,
  bookNext,
  daysBetween,
  lastRecorded,
  nextCritical,
  nextWindow,
  unrecorded,
} from "@/lib/schedulePlan";
import { ScheduleViews } from "./ScheduleViews";

// Force a fresh server-side render every request — no static or RSC payload
// caching. Cheap insurance for an auth-gated, data-dependent page.
export const dynamic = "force-dynamic";

/** Today on site (Vancouver), as YYYY-MM-DD. */
function siteToday(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/Vancouver" });
}

function signedDays(n: number): string {
  if (n === 0) return "same day";
  return `${n > 0 ? "+" : "−"}${Math.abs(n)} day${Math.abs(n) === 1 ? "" : "s"}`;
}

const BOOKING_TONE: Record<string, string> = {
  to_book: "border-amber-700 text-amber-800",
  requested: "border-amber-700 text-amber-800",
  booked: "border-green-700 text-green-800",
  received: "border-green-700 text-green-800",
  done: "border-slate-400 text-slate-600",
};

export default async function SchedulePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ v?: string }>;
}) {
  const { id } = await params;
  const { v } = await searchParams;
  // Both lib calls go through the server-side `api()` helper, which throws
  // on any non-2xx. Wrap both so we render a clear error card instead of
  // crashing into Vercel's generic "page couldn't load" overlay — the user
  // can read the upstream error and we get a log trace either way.
  let list: Awaited<ReturnType<typeof listSchedules>> = [];
  let listErr: string | null = null;
  try {
    list = await listSchedules(id);
  } catch (e) {
    listErr = String(e);
  }

  if (listErr) {
    return (
      <div className="rounded border border-red-300 bg-red-50 p-4 text-sm text-red-800">
        <div className="font-semibold mb-1">Couldn&apos;t load schedules list</div>
        <pre className="whitespace-pre-wrap break-words text-xs">{listErr}</pre>
      </div>
    );
  }
  const latest = list[list.length - 1];

  if (!latest) {
    return (
      <div className="text-sm text-slate-500">
        No schedule for this project yet.
      </div>
    );
  }
  const chosen = list.find((s) => String(s.version_number) === v) ?? latest;
  const baseline = list.find((s) => s.is_baseline) ?? null;
  // What the client portal shows: the latest version shared with the client.
  const clientSees = [...list].reverse().find((s) => s.client_visible !== false) ?? null;

  let sched: Awaited<ReturnType<typeof getSchedule>>;
  try {
    sched = await getSchedule(id, chosen.id);
  } catch (e) {
    return (
      <div className="rounded border border-red-300 bg-red-50 p-4 text-sm text-red-800">
        <div className="font-semibold mb-1">
          Couldn&apos;t load schedule v{chosen.version_number}
        </div>
        <pre className="whitespace-pre-wrap break-words text-xs">
          {String(e)}
        </pre>
        <p className="mt-2 text-xs text-red-700">
          Schedule ID: <code>{chosen.id}</code>
        </p>
      </div>
    );
  }
  // Pass the critical-path codes as a plain string[] across the
  // Server/Client component boundary — Set props serialize poorly in
  // some Next 16 + React 19 production builds, and a failure here is
  // *after* the try/catch above so it crashes into Vercel's overlay
  // instead of our red error card. GanttChart builds its own Set.
  const cpCodes: string[] = Array.isArray(sched.critical_path_activity_codes)
    ? sched.critical_path_activity_codes
    : [];
  const cpSet = new Set(cpCodes);

  // Actual dates and bookings are extra, not the page: if either fails, say
  // so and still show the plan (never swallow it into an empty list).
  const actuals: Record<string, ActivityActual> = {};
  let actualsErr: string | null = null;
  try {
    for (const a of (await getActuals(id)).actuals) actuals[a.activity_code] = a;
  } catch (e) {
    actualsErr = String(e);
  }
  let bookings: BookingItem[] = [];
  let bookingsErr: string | null = null;
  try {
    bookings = (await listBookings(id)).items;
  } catch (e) {
    bookingsErr = String(e);
  }

  // Baseline comparison, per phase: when each phase finishes in this version
  // against the baseline. Only when looking at a version other than it.
  let baselinePhases: { phase: string; base: string | null; now: string | null }[] = [];
  let baselineErr: string | null = null;
  if (baseline && baseline.id !== chosen.id) {
    try {
      const b = await getSchedule(id, baseline.id);
      const lastFinish = (acts: typeof sched.activities) => {
        const m: Record<string, string> = {};
        for (const a of acts) if (a.finish_date && (!m[a.phase] || a.finish_date > m[a.phase])) m[a.phase] = a.finish_date;
        return m;
      };
      const bm = lastFinish(b.activities);
      const nm = lastFinish(sched.activities);
      const order: string[] = [];
      for (const a of sched.activities) if (!order.includes(a.phase)) order.push(a.phase);
      for (const p of Object.keys(bm)) if (!order.includes(p)) order.push(p);
      baselinePhases = order.map((phase) => ({ phase, base: bm[phase] ?? null, now: nm[phase] ?? null }));
    } catch (e) {
      baselineErr = String(e);
    }
  }

  const today = siteToday();
  const isCurrent = chosen.id === latest.id;
  const recorded = lastRecorded(actuals);
  const { shouldHaveStarted, shouldHaveFinished } = unrecorded(sched.activities, actuals, today);
  const missing = shouldHaveStarted.length + shouldHaveFinished.length;
  const assumed = assumedDone(sched.activities, actuals);
  const next = nextCritical(sched.activities, actuals);
  const book = bookNext(bookings);
  const upcoming = nextWindow(sched.activities, actuals, bookings, today);
  const vsBaseline =
    baseline && baseline.deterministic_finish_date && sched.deterministic_finish_date
      ? daysBetween(baseline.deterministic_finish_date, sched.deterministic_finish_date)
      : null;

  // Phase grouping
  const byPhase: Record<string, typeof sched.activities> = {};
  for (const a of sched.activities) {
    (byPhase[a.phase] ??= []).push(a);
  }

  const card = "rounded border border-slate-200 bg-white p-4";
  const btn = "inline-flex min-h-9 items-center rounded border border-slate-800 px-3 text-xs font-medium text-slate-900 hover:bg-slate-100";
  const base = `/projects/${id}`;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">
            Plan · v{chosen.version_number}
            {chosen.is_baseline && <span className="ml-2 align-middle rounded border border-slate-400 px-1.5 py-0.5 text-[10px] font-medium text-slate-700">BASELINE</span>}
            {!isCurrent && <span className="ml-2 align-middle rounded border border-amber-700 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">NOT THE CURRENT VERSION</span>}
            {clientSees && clientSees.id !== chosen.id && (
              <span className="ml-2 align-middle rounded border border-slate-300 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
                CLIENT SEES v{clientSees.version_number}
              </span>
            )}
          </h1>
          <p className="text-sm text-slate-600 mt-1">
            {sched.activities.length} activities · {cpSet.size} on critical path · made {fmtDate(chosen.created_at?.slice(0, 10))}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <details className="relative">
            <summary className={`${btn} cursor-pointer list-none`}>Version v{chosen.version_number} ▾</summary>
            <div className="absolute right-0 z-20 mt-1 min-w-56 rounded-md border border-slate-300 bg-white py-1 shadow-md">
              {[...list].reverse().map((s) => (
                <Link
                  key={s.id}
                  href={s.id === latest.id ? `${base}/workbench/schedule` : `${base}/workbench/schedule?v=${s.version_number}`}
                  className={`block px-3 py-2 text-sm hover:bg-slate-100 ${s.id === chosen.id ? "font-semibold" : ""}`}
                >
                  v{s.version_number} · {fmtDate(s.created_at?.slice(0, 10))}
                  {s.id === latest.id && " (current)"}
                  {s.is_baseline && " (baseline)"}
                  {clientSees?.id === s.id && " (client sees)"}
                </Link>
              ))}
            </div>
          </details>
          <a href={`${base}/workbench/schedule/edit`} className={btn}>Edit &amp; recompute</a>
          <Link href={`${base}/schedule/record`} className="inline-flex min-h-9 items-center rounded bg-slate-900 px-3 text-xs font-medium text-white hover:bg-slate-700">
            Record what happened
          </Link>
        </div>
      </header>

      {actualsErr ? (
        <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          Couldn&apos;t load what was recorded: {actualsErr}
        </div>
      ) : isCurrent && missing > 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-amber-700 bg-amber-50 p-3 text-sm">
          <span>
            <b>{recorded ? `Last recorded ${fmtDate(recorded)}.` : "Nothing recorded yet."}</b>{" "}
            {shouldHaveFinished.length > 0 && <>{shouldHaveFinished.length} started but not recorded as finished. </>}
            {shouldHaveStarted.length > 0 && <>{shouldHaveStarted.length} should have started by today with nothing on record. </>}
            The finish date below assumes they went to plan.
            {assumed.size > 0 && (
              <span className="block mt-1 text-xs text-slate-700">
                {assumed.size} earlier activit{assumed.size === 1 ? "y is" : "ies are"} counted as done because later work has started.
              </span>
            )}
          </span>
          <Link href={`${base}/schedule/record`} className={btn}>Record them</Link>
        </div>
      ) : isCurrent ? (
        <p className="text-xs text-slate-600">
          {recorded ? <>Up to date: last recorded {fmtDate(recorded)}.</> : <>Nothing recorded yet.</>}
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className={card}>
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Finish</div>
          <div className="text-lg font-semibold mt-1">{fmtDate(sched.deterministic_finish_date)}</div>
          <div className="text-xs text-slate-600">likely by {fmtDate(sched.p80_finish_date)} (P80)</div>
        </div>
        <div className={card}>
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Against baseline</div>
          {baseline ? (
            chosen.id === baseline.id ? (
              <div className="text-sm mt-1 text-slate-600">This is the baseline.</div>
            ) : (
              <>
                <div className="text-lg font-semibold mt-1">{vsBaseline == null ? "—" : signedDays(vsBaseline)}</div>
                <div className="text-xs text-slate-600">v{baseline.version_number} finished {fmtDate(baseline.deterministic_finish_date)}</div>
              </>
            )
          ) : (
            <div className="text-sm mt-1 text-slate-600">No baseline set.</div>
          )}
        </div>
        <div className={card}>
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Next on critical path</div>
          {next ? (
            <>
              <div className="text-sm font-semibold mt-1">{next.name}</div>
              <div className="text-xs text-slate-600">
                {fmtDate(next.start_date)} – {fmtDate(next.finish_date)}
                {actuals[next.activity_code]?.actual_start && <> · started {fmtDate(actuals[next.activity_code].actual_start)}</>}
              </div>
            </>
          ) : (
            <div className="text-sm mt-1 text-slate-600">Nothing left on it.</div>
          )}
        </div>
        <div className={`${card} ${book?.late ? "border-amber-700" : ""}`}>
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Book next</div>
          {bookingsErr ? (
            <div className="text-xs mt-1 text-red-700">Couldn&apos;t load bookings: {bookingsErr}</div>
          ) : book ? (
            <>
              <div className="text-sm font-semibold mt-1">{book.title}</div>
              <div className={`text-xs ${book.late ? "text-amber-800 font-medium" : "text-slate-600"}`}>
                book by {fmtDate(book.book_by)}{book.late && " · late"}
              </div>
            </>
          ) : (
            <div className="text-sm mt-1 text-slate-600">Nothing waiting to book.</div>
          )}
        </div>
      </div>

      <ScheduleViews
        actuals={actuals}
        activities={sched.activities}
        inspections={sched.inspections}
        criticalCodes={cpCodes}
        projectStart={sched.project_start}
        projectEnd={sched.p50_finish_date}
      />
      <p className="text-xs text-slate-600">Green bars under the plan are what actually happened, from site reports and typed entries.</p>

      {isCurrent && (
        <section className="rounded border border-slate-200 bg-white">
          <h2 className="px-4 pt-3 pb-2 text-sm font-semibold uppercase tracking-wide text-slate-700">
            Next two weeks · bookings sit on their activity
          </h2>
          {upcoming.length === 0 ? (
            <p className="px-4 pb-3 text-sm text-slate-600">Nothing planned in the next two weeks.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-sm">
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
                  <tr>
                    <th className="px-3 py-2 text-left">When (plan)</th>
                    <th className="px-3 py-2 text-left">Activity</th>
                    <th className="px-3 py-2 text-left">On record</th>
                    <th className="px-3 py-2 text-left">Bookings</th>
                  </tr>
                </thead>
                <tbody>
                  {upcoming.map(({ activity: a, bookings: bs, overdue }) => {
                    const act = actuals[a.activity_code];
                    return (
                      <tr key={a.activity_code} className="border-t border-slate-100 align-top">
                        <td className="px-3 py-2 whitespace-nowrap">
                          {a.start_date === a.finish_date ? fmtDate(a.start_date) : <>{fmtDate(a.start_date)} – {fmtDate(a.finish_date)}</>}
                          {overdue && <div className="text-[11px] font-medium text-amber-800">past its planned finish</div>}
                        </td>
                        <td className="px-3 py-2">
                          <span className={a.is_critical ? "font-semibold" : ""}>{a.name}</span>
                          <span className="ml-1 text-xs text-slate-500">{a.activity_code}</span>
                        </td>
                        <td className="px-3 py-2 text-xs">
                          {act?.actual_start ? (
                            <>started {fmtDate(act.actual_start)}{act.from_draft && <span className="ml-1 text-amber-700">draft</span>}</>
                          ) : act?.reason ? (
                            <span className="text-amber-800" title={act.reason}>not started: {act.reason}</span>
                          ) : (
                            <span className="text-slate-500">nothing</span>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex flex-wrap gap-1.5">
                            {bs.length === 0 && <span className="text-xs text-slate-400">—</span>}
                            {bs.map((b) => (
                              <Link
                                key={b.id}
                                href={`${base}/milestones`}
                                className={`rounded border px-1.5 py-0.5 text-[11px] font-medium ${BOOKING_TONE[b.status] ?? "border-slate-400 text-slate-700"}`}
                                title={b.title}
                              >
                                {b.title.length > 34 ? b.title.slice(0, 33) + "…" : b.title}: {BOOKING_LABEL[b.status] ?? b.status}
                                {b.status === "to_book" || b.status === "requested" ? (b.book_by ? ` · book by ${fmtDate(b.book_by)}` : "") : b.booked_for ? ` · ${fmtDate(b.booked_for)}` : ""}
                              </Link>
                            ))}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {baseline && baseline.id !== chosen.id && (
        <details className="rounded border border-slate-200 bg-white">
          <summary className="cursor-pointer px-4 py-3 text-sm font-semibold uppercase tracking-wide text-slate-700">
            Compare with baseline v{baseline.version_number}, by phase
          </summary>
          {baselineErr ? (
            <p className="px-4 pb-3 text-sm text-red-700">Couldn&apos;t load the baseline: {baselineErr}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-sm">
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
                  <tr>
                    <th className="px-3 py-2 text-left">Phase</th>
                    <th className="px-3 py-2 text-left">Baseline finish</th>
                    <th className="px-3 py-2 text-left">v{chosen.version_number} finish</th>
                    <th className="px-3 py-2 text-right">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {baselinePhases.map((p) => (
                    <tr key={p.phase} className="border-t border-slate-100">
                      <td className="px-3 py-2">{p.phase}</td>
                      <td className="px-3 py-2">{p.base ? fmtDate(p.base) : <span className="text-slate-500">not in baseline</span>}</td>
                      <td className="px-3 py-2">{p.now ? fmtDate(p.now) : <span className="text-slate-500">removed</span>}</td>
                      <td className="px-3 py-2 text-right">{p.base && p.now ? signedDays(daysBetween(p.base, p.now)) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </details>
      )}

      <details className="rounded border border-slate-200 bg-white">
        <summary className="cursor-pointer px-4 py-3 text-sm font-semibold uppercase tracking-wide text-slate-700">
          All activities by phase
        </summary>
        <div className="px-4 pb-4">
          {Object.entries(byPhase).map(([phase, acts]) => (
            <div key={phase} className="mb-4">
              <h3 className="text-sm font-medium text-slate-700 mb-1">{phase}</h3>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-xs border border-slate-200">
                  <thead className="bg-slate-50">
                    <tr>
                      <th className="px-2 py-1 text-left">ID</th>
                      <th className="px-2 py-1 text-left">Name</th>
                      <th className="px-2 py-1 text-left">Predecessors</th>
                      <th className="px-2 py-1 text-right">O/M/P (wd)</th>
                      <th className="px-2 py-1">Start</th>
                      <th className="px-2 py-1">Finish</th>
                      <th className="px-2 py-1">Actual start</th>
                      <th className="px-2 py-1">Actual finish</th>
                      <th className="px-2 py-1 text-right">TF</th>
                      <th className="px-2 py-1">Critical</th>
                    </tr>
                  </thead>
                  <tbody>
                    {acts.map((a) => {
                      const act = actuals[a.activity_code];
                      return (
                        <tr key={a.id} className="border-t border-slate-100">
                          <td className="px-2 py-1 font-medium">{a.activity_code}</td>
                          <td className="px-2 py-1">{a.name}</td>
                          <td className="px-2 py-1 text-slate-500">
                            {(a.predecessor_codes || []).join(", ") || "—"}
                          </td>
                          <td className="px-2 py-1 text-right text-slate-500">
                            {a.optimistic_wd ?? "—"} / {a.most_likely_wd ?? "—"} / {a.pessimistic_wd ?? "—"}
                          </td>
                          <td className="px-2 py-1 text-slate-500 whitespace-nowrap">{fmtDate(a.start_date)}</td>
                          <td className="px-2 py-1 text-slate-500 whitespace-nowrap">{fmtDate(a.finish_date)}</td>
                          <td className="px-2 py-1 whitespace-nowrap">
                            {fmtDate(act?.actual_start ?? null)}
                            {act?.from_draft && <span className="ml-1 text-[10px] text-amber-700">draft</span>}
                          </td>
                          <td className="px-2 py-1 whitespace-nowrap">
                            {act?.actual_finish
                              ? fmtDate(act.actual_finish)
                              : act?.actual_start
                                ? `${act.percent_complete ?? 0}%`
                                : act?.reason
                                  ? <span className="text-amber-800" title={act.reason ?? ""}>not started</span>
                                  : "—"}
                          </td>
                          <td className="px-2 py-1 text-right">{a.total_float_wd ?? "—"}</td>
                          <td className="px-2 py-1">
                            {a.is_critical ? (
                              <span className="inline-block px-1.5 py-0.5 rounded bg-red-100 text-red-800 text-[10px] font-medium">
                                CRITICAL
                              </span>
                            ) : a.is_milestone ? (
                              <span className="inline-block px-1.5 py-0.5 rounded bg-slate-200 text-slate-800 text-[10px] font-medium">
                                MILESTONE
                              </span>
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      </details>

      <details className="rounded border border-slate-200 bg-white">
        <summary className="cursor-pointer px-4 py-3 text-sm font-semibold uppercase tracking-wide text-slate-700">
          Inspections in this version
        </summary>
        <div className="overflow-x-auto px-4 pb-4">
          <table className="w-full min-w-[520px] text-sm border border-slate-200">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-3 py-2 text-left">Authority</th>
                <th className="px-3 py-2 text-left">Type</th>
                <th className="px-3 py-2 text-right">Lead time (d)</th>
                <th className="px-3 py-2 text-left">Gates</th>
              </tr>
            </thead>
            <tbody>
              {sched.inspections.map((i) => (
                <tr key={i.id} className="border-t border-slate-100">
                  <td className="px-3 py-2 font-medium">{i.authority}</td>
                  <td className="px-3 py-2">{i.inspection_type}</td>
                  <td className="px-3 py-2 text-right">{i.lead_time_days ?? "—"}</td>
                  <td className="px-3 py-2 text-slate-600">{i.gates ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

    </div>
  );
}
