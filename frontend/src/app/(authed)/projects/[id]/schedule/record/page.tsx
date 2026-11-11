import Link from "next/link";
import {
  listSchedules, getSchedule, getActuals, getReplanPreview, fmtDate, type ActivityActual, type ReplanPreview,
} from "@/lib/workbench";
import { addDays, assumedDone, unrecorded, type PlanActivity } from "@/lib/schedulePlan";

import RecordForm, { type RecordItem } from "./RecordForm";
import UpdatePlan from "./UpdatePlan";

export const dynamic = "force-dynamic";

/** Schedule › Record what happened.
 *
 *  What the plan expected by today with nothing on record, answered here as
 *  Done / Started / Not started (RecordForm), then what is on record (site
 *  reports, drafts flagged, and typed answers). */
export default async function RecordPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/Vancouver" });

  let err: string | null = null;
  let acts: Awaited<ReturnType<typeof getSchedule>>["activities"] = [];
  let version: number | null = null;
  const actuals: Record<string, ActivityActual> = {};
  try {
    const list = await listSchedules(id);
    const latest = list[list.length - 1];
    if (latest) {
      version = latest.version_number;
      acts = (await getSchedule(id, latest.id)).activities;
    }
    for (const a of (await getActuals(id)).actuals) actuals[a.activity_code] = a;
  } catch (e) {
    err = String(e);
  }

  if (err) {
    return (
      <div className="rounded border border-red-300 bg-red-50 p-4 text-sm text-red-800">
        <div className="font-semibold mb-1">Couldn&apos;t load the schedule or what was recorded</div>
        <pre className="whitespace-pre-wrap break-words text-xs">{err}</pre>
      </div>
    );
  }
  if (version == null) {
    return <div className="text-sm text-slate-500">No schedule for this project yet.</div>;
  }

  // Update the plan (office only): both rules for unanswered work, so the
  // screen can show the range without another round trip.
  let replan: { keep: ReplanPreview; push: ReplanPreview } | null = null;
  let replanErr: string | null = null;
  try {
    const [keep, push] = await Promise.all([getReplanPreview(id, "keep"), getReplanPreview(id, "push")]);
    replan = { keep, push };
  } catch (e) {
    replanErr = String(e);
  }

  const { shouldHaveStarted, shouldHaveFinished } = unrecorded(acts, actuals, today);
  const byCode = new Map(acts.map((a) => [a.activity_code, a]));
  const assumed = [...assumedDone(acts, actuals)]
    .map((c) => byCode.get(c))
    .filter((a): a is NonNullable<typeof a> => !!a)
    .sort((x, y) => (x.start_date ?? "").localeCompare(y.start_date ?? ""));
  const onRecord = Object.values(actuals).sort((x, y) =>
    (byCode.get(x.activity_code)?.start_date ?? "").localeCompare(byCode.get(y.activity_code)?.start_date ?? ""),
  );

  const item = (a: PlanActivity): RecordItem => {
    const r = actuals[a.activity_code];
    const hint = r?.actual_start
      ? `Started ${fmtDate(r.actual_start)}${r.from_draft ? " (draft site report)" : ""}; finish not on record.`
      : r?.reason
        ? `Site report ${r.last_reported ? fmtDate(r.last_reported) : ""}: "${r.reason}"`
        : null;
    return {
      code: a.activity_code, name: a.name, critical: !!a.is_critical,
      plan: `${fmtDate(a.start_date)} – ${fmtDate(a.finish_date)}`,
      recordedStart: r?.actual_start ?? null, hint,
    };
  };
  const due = [...shouldHaveFinished, ...shouldHaveStarted].map(item);
  const dueCodes = new Set(due.map((d) => d.code));
  const others = acts
    .filter((a) => !a.is_milestone && !dueCodes.has(a.activity_code) && !actuals[a.activity_code]?.actual_finish
      && (a.start_date ?? "9999") <= addDays(today, 14))
    .sort((x, y) => (x.start_date ?? "").localeCompare(y.start_date ?? ""))
    .map(item);

  const row = "border-t border-slate-100";
  const th = "px-3 py-2 text-left";

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold">Record what happened</h1>
        <p className="mt-1 text-sm text-slate-600">
          Against schedule v{version}. Facts come from site reports (drafts count, marked) and from what you enter here.
          Entering facts doesn&apos;t change the plan until you save a new version under Update the plan.
        </p>
      </header>

      <RecordForm projectId={id} due={due} others={others} today={today} />

      {replan ? (
        <UpdatePlan projectId={id} keep={replan.keep} push={replan.push} answerable={acts.filter((a) => !a.is_milestone).map((a) => a.activity_code)} />
      ) : (
        <div className="rounded border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
          Couldn&apos;t work out the plan update: <span className="text-xs">{replanErr}</span>
        </div>
      )}

      <section className="rounded border border-slate-200 bg-white">
        <h2 className="px-4 pt-3 pb-2 text-sm font-semibold uppercase tracking-wide text-slate-700">
          On record · {onRecord.length}
        </h2>
        {onRecord.length === 0 ? (
          <p className="px-4 pb-3 text-sm text-slate-600">Nothing recorded yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
                <tr><th className={th}>Activity</th><th className={th}>Status</th><th className={th}>Started</th><th className={th}>Finished</th><th className={th}>From</th></tr>
              </thead>
              <tbody>
                {onRecord.map((a) => (
                  <tr key={a.activity_code} className={row}>
                    <td className="px-3 py-2">
                      {byCode.get(a.activity_code)?.name ?? <span className="text-slate-500">not in v{version}</span>}{" "}
                      <span className="text-xs text-slate-500">{a.activity_code}</span>
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {a.status === "done" ? "Done" : a.status === "started" ? `Started · ${a.percent_complete ?? 0}%` : "Not started"}
                      {a.reason && <div className="text-amber-800">{a.reason}</div>}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">{fmtDate(a.actual_start)}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{fmtDate(a.actual_finish)}</td>
                    <td className="px-3 py-2 text-xs">
                      {a.source === "typed" ? "entered" : a.source === "both" ? "site report + entered" : "site report"}
                      {a.last_reported && <> · {fmtDate(a.last_reported)}</>}
                      {a.from_draft && <span className="ml-1 text-amber-700">draft</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {assumed.length > 0 && (
        <details className="rounded border border-slate-200 bg-white">
          <summary className="cursor-pointer px-4 py-3 text-sm font-semibold uppercase tracking-wide text-slate-700">
            Counted as done because later work started · {assumed.length}
          </summary>
          <p className="px-4 text-xs text-slate-600">No dates on record for these. Recording the real dates will replace the assumption.</p>
          <ul className="px-4 pb-3 pt-2 text-sm">
            {assumed.map((a) => (
              <li key={a.activity_code} className="py-0.5">
                {a.name} <span className="text-xs text-slate-500">{a.activity_code} · plan {fmtDate(a.start_date)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <p className="text-xs text-slate-600">
        Site reports are filed from <Link href={`/projects/${id}/site`} className="text-blue-700">Site</Link>.
      </p>
    </div>
  );
}
