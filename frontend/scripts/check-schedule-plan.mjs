/**
 * Schedule › Plan must say what the site reports show, not what the plan
 * assumed. Shapes from 1234 on 2026-10-29: forming A100 started 16 Oct (draft
 * report), pour A107 "delayed" on 21 Oct, nothing since; the sketch claimed
 * "nothing recorded since 13 Oct", which was wrong.
 */
import { assumedDone, bookNext, clientDraft, finishDraft, daysBetween, entryBody, entryProblem, lastRecorded, nextCritical, nextWindow, unrecorded } from "../src/lib/schedulePlan.ts";

let bad = 0;
const eq = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { bad++; console.error(`✗ ${name}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`); }
};

const act = (code, start, finish, o = {}) => ({
  activity_code: code, name: code, phase: "Foundation", start_date: start, finish_date: finish,
  is_critical: true, is_milestone: false, ...o,
});
const acts = [
  act("A70", "2026-10-09", "2026-10-09", { is_milestone: true }),
  act("A75", "2026-10-14", "2026-10-14", { predecessor_codes: ["A70"] }),
  act("A100", "2026-10-15", "2026-10-16", { predecessor_codes: ["A75"] }),
  act("A105", "2026-10-19", "2026-10-19", { is_milestone: true, predecessor_codes: ["A100"] }),
  act("A107", "2026-10-20", "2026-10-21", { predecessor_codes: ["A105"] }),
  act("A110", "2026-10-22", "2026-10-26", { predecessor_codes: ["A107"] }),
  act("A127", "2026-11-02", "2026-11-03", { is_critical: false }),
  act("A260", "2026-11-27", "2026-11-30", { is_critical: false }),
];
const actuals = {
  A100: { actual_start: "2026-10-16", actual_finish: null, last_reported: "2026-10-16", status: "started" },
  A107: { actual_start: null, actual_finish: null, last_reported: "2026-10-21", status: "not_started" },
};
const today = "2026-10-29";

eq("work before a started activity is assumed done (A75 delivery before A100 forming)",
  [...assumedDone(acts, actuals)].sort(), ["A70", "A75"]);
eq("nothing started, nothing assumed", [...assumedDone(acts, {})], []);
eq("last recorded is the 21 Oct report, not 13 Oct", lastRecorded(actuals), "2026-10-21");
eq("empty actuals have no last date", lastRecorded({}), null);

const u = unrecorded(acts, actuals, today);
eq("started but past its finish", u.shouldHaveFinished.map((a) => a.activity_code), ["A100"]);
eq("due to start, nothing recorded (milestones left out)", u.shouldHaveStarted.map((a) => a.activity_code), ["A107", "A110"]);
eq("future work is not flagged", unrecorded([act("X", "2026-11-01", "2026-11-02")], {}, today).shouldHaveStarted, []);

eq("next on the critical path is the unfinished forming", nextCritical(acts, actuals)?.activity_code, "A100");
eq("a finished activity is skipped",
  nextCritical(acts, { ...actuals, A100: { ...actuals.A100, actual_finish: "2026-10-22" } })?.activity_code, "A107");

const bookings = [
  { id: "1", title: "GEO walls", status: "to_book", activity_code: "A110", book_by: "2026-10-22", need_by: "2026-10-27", booked_for: null, late: true },
  { id: "2", title: "DNV field", status: "booked", activity_code: "A125", book_by: "2026-10-15", need_by: "2026-10-20", booked_for: "2026-10-20", late: false },
  { id: "3", title: "Survey", status: "requested", activity_code: "A127", book_by: "2026-10-30", need_by: null, booked_for: null, late: false },
  { id: "4", title: "Skip", status: "not_needed", activity_code: "A110", book_by: "2026-10-01", need_by: null, booked_for: null, late: false },
];
eq("book next = earliest open book-by (booked and not-needed ignored)", bookNext(bookings)?.id, "1");
eq("nothing open, nothing to book", bookNext([bookings[1]]), null);

const w = nextWindow(acts, actuals, bookings, today);
eq("window: overdue unfinished first, then the next two weeks",
  w.map((r) => [r.activity.activity_code, r.overdue]),
  [["A100", true], ["A105", true], ["A107", true], ["A110", true], ["A127", false]]);
eq("bookings ride on their activity, not-needed dropped", w.find((r) => r.activity.activity_code === "A110").bookings.map((b) => b.id), ["1"]);

eq("v7 vs baseline v2 finish", daysBetween("2027-01-19", "2027-01-15"), -4);

// ── Record what happened: answers ──
eq("done needs a finish", entryProblem({ status: "done" }, null, today), "Say when it finished.");
eq("forming done 22 Oct against the 16 Oct start on record", entryProblem({ status: "done", finish: "2026-10-22" }, "2026-10-16", today), null);
eq("finish before the recorded start is caught before saving",
  entryProblem({ status: "done", finish: "2026-10-14" }, "2026-10-16", today), "Finish is before the start (2026-10-16).");
eq("started with a start on record needs no date", entryProblem({ status: "started" }, "2026-10-16", today), null);
eq("started with nothing on record needs a date", entryProblem({ status: "started" }, null, today), "Say when it started.");
eq("not started needs a reason", entryProblem({ status: "not_started", reason: "  " }, null, today), "Pick a reason.");
eq("no future dates", entryProblem({ status: "started", start: "2026-10-30" }, null, today), "That date is in the future.");
eq("done with no start anywhere takes the finish as its start",
  entryBody({ status: "done", finish: "2026-10-22" }, null),
  { status: "done", actual_finish: "2026-10-22", percent_complete: 100, reason: null, actual_start: "2026-10-22" });
eq("done keeps the start already on record", "actual_start" in entryBody({ status: "done", finish: "2026-10-22" }, "2026-10-16"), false);
eq("not started sends only the reason", entryBody({ status: "not_started", reason: " Weather " }, null), { status: "not_started", reason: "Weather" });
const draft = clientDraft([
  { name: "Form Footings", entry: { status: "done", finish: "2026-10-22" }, recordedStart: "2026-10-16" },
  { name: "Pour Footings", entry: { status: "started", percent: 40 }, recordedStart: "2026-10-23" },
  { name: "Install GRAF EcoBloc tank", entry: { status: "not_started", reason: "Waiting on material" }, recordedStart: null },
]);
eq("client draft: plain dates, reasons, no codes or percentages", draft.split("\n").slice(2, 5), [
  "- Form Footings: finished Thu, Oct 22.",
  "- Pour Footings: under way since Fri, Oct 23.",
  "- Install GRAF EcoBloc tank: not started yet (waiting on material).",
]);
eq("client draft never carries an activity code", /\bA\d{2,3}\b/.test(draft), false);

// ── Update the plan: client message ──
eq("later finish says how much later, with the year",
  finishDraft("2027-01-15", "2027-01-19").split("\n")[2],
  "We now expect to finish Tue, Jan 19, 2027, 4 days later than the Fri, Jan 15, 2027 we gave you.");
eq("same finish says on track", finishDraft("2027-01-15", "2027-01-15").split("\n")[2], "We're still on track to finish Fri, Jan 15, 2027.");
eq("earlier finish, singular day", finishDraft("2027-01-15", "2027-01-14").split("\n")[2].includes("1 day earlier"), true);

if (bad) { console.error(`check-schedule-plan: ${bad} failing`); process.exit(1); }
console.log("✓ schedule plan facts (last recorded, unrecorded work, next critical, bookings)");
