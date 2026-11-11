import Link from "next/link";
import { loadOr } from "@/lib/loadOr";
import { LoadFailed } from "@/components/LoadFailed";
import { listCashFlows, getCashFlow, getSovProgress, fmtDate, fmtMoney } from "@/lib/workbench";
import { StaleBanner } from "../StaleBanner";
import { CashPositionChart } from "./CashPositionChart";
import { CashSovSwitch, NotBuiltYet } from "../CashSovSwitch";

export default async function CashFlowPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ scenario?: string }>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const scenario = sp.scenario === "combined" ? "combined" : "base";

  const res = await loadOr("cashflow", listCashFlows(id));
  if (res.failed) {
    return (
      <div className="space-y-4">
        <CashSovSwitch projectId={id} on="cashflow" />
        <LoadFailed what="the cash flow" />
      </div>
    );
  }
  const all = res.data ?? [];
  const chosen = all.find((c) => c.scenario === scenario);

  if (!chosen) {
    return (
      <div className="space-y-4">
        <CashSovSwitch projectId={id} on="cashflow" />
        <NotBuiltYet projectId={id} what="cash flow" />
      </div>
    );
  }

  const cf = await getCashFlow(id, chosen.id);
  // Money in/out by month (R2Billing), from the latest cash flow.
  const prog = await loadOr("cash by month", getSovProgress(id));
  const months = prog.data?.progress?.months ?? [];
  const peak = Math.max(1, ...months.flatMap((m) => [m.in, m.out]));
  const hasOption = all.some((c) => c.scenario === "combined");
  // Built by Contract › Billing plan (2026-11-02): the holdback is its own
  // contract stage, everything is before GST, and costs are an assumption.
  const kd = (cf.key_dates ?? {}) as Record<string, string | number | null>;
  const fromPlan = kd.basis === "billing-plan";

  const card = "rounded border border-slate-200 bg-white p-4";
  const tabActive = "px-3 py-1.5 rounded bg-slate-900 text-white text-xs font-medium";
  const tabIdle = "px-3 py-1.5 rounded bg-slate-100 text-slate-700 text-xs hover:bg-slate-200";

  return (
    <div className="space-y-5">
      <CashSovSwitch projectId={id} on="cashflow" />
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-xl font-semibold">Cash Flow</h1>
          <p className="text-sm text-slate-600 mt-1">
            Version {cf.version_number}
            {hasOption && <> · Scenario: <span className="font-medium uppercase">{scenario}</span></>}
            {fromPlan && <> · from the billing plan, schedule v{kd.schedule_version}</>}
          </p>
          {fromPlan && cf.notes && <p className="text-xs text-slate-500 mt-1 max-w-3xl">{cf.notes}</p>}
        </div>
        {hasOption && <div className="flex gap-2">
          <Link
            href={`/projects/${id}/workbench/cashflow?scenario=base`}
            className={scenario === "base" ? tabActive : tabIdle}
          >
            Base
          </Link>
          <Link
            href={`/projects/${id}/workbench/cashflow?scenario=combined`}
            className={scenario === "combined" ? tabActive : tabIdle}
          >
            Base + Option
          </Link>
        </div>}
      </header>

      <StaleBanner
        projectId={id}
        basedOnEstimateId={cf.based_on_estimate_id}
        isStale={cf.is_stale}
      />

      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <div className={card}>
          <div className="text-xs text-slate-500">Money in (before GST)</div>
          <div className="text-lg font-medium mt-1">{fmtMoney(cf.total_pretax)}</div>
        </div>
        <div className={card}>
          <div className="text-xs text-slate-500">Final cash position</div>
          <div className="text-lg font-medium mt-1 text-emerald-600">
            {fmtMoney(cf.final_cash_position)}
          </div>
          <div className="text-xs text-slate-500 mt-0.5">
            {fromPlan ? `margin left if costs land at ${kd.cost_share_pct}% of budget` : "= GC profit margin"}
          </div>
        </div>
        <div className={card}>
          <div className="text-xs text-slate-500">Min cash position</div>
          <div className="text-lg font-medium mt-1 text-red-600">
            {fmtMoney(cf.min_cash_position)}
          </div>
          <div className="text-xs text-slate-500 mt-0.5">
            W/E {fmtDate(cf.min_position_week_end)}
          </div>
        </div>
        <div className={card}>
          <div className="text-xs text-slate-500">Bridge financing needed</div>
          <div className="text-lg font-medium mt-1">
            {fmtMoney(cf.bridge_financing_required)}
          </div>
        </div>
      </div>

      <section className={card}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700 mb-2">
          Payment terms
        </h2>
        {fromPlan ? (
          <table className="w-full text-sm">
            <tbody>
              <tr>
                <td className="py-1 text-slate-600">Client pays BRIO</td>
                <td className="py-1 text-right">{cf.owner_net_days} days after each bill</td>
              </tr>
              <tr>
                <td className="py-1 text-slate-600">Holdback</td>
                <td className="py-1 text-right">
                  its own contract stage, paid {cf.lien_release_days_post_substantial} days after substantial completion
                  {kd.holdback_release ? ` (${fmtDate(String(kd.holdback_release))})` : ""}
                </td>
              </tr>
              <tr>
                <td className="py-1 text-slate-600">Money out</td>
                <td className="py-1 text-right">bills entered + {kd.cost_share_pct}% of what&apos;s left of each division&apos;s budget</td>
              </tr>
            </tbody>
          </table>
        ) : (
        <table className="w-full text-sm">
          <tbody>
            <tr>
              <td className="py-1 text-slate-600">Owner pays BRIO</td>
              <td className="py-1 text-right">net-{cf.owner_net_days} days</td>
            </tr>
            <tr>
              <td className="py-1 text-slate-600">BC Builders Lien Act holdback</td>
              <td className="py-1 text-right">{cf.lien_holdback_pct}% per draw</td>
            </tr>
            <tr>
              <td className="py-1 text-slate-600">Lien holdback release</td>
              <td className="py-1 text-right">
                {cf.lien_release_days_post_substantial} days post substantial completion
              </td>
            </tr>
            <tr>
              <td className="py-1 text-slate-600">Deficiency holdback release</td>
              <td className="py-1 text-right">
                {cf.deficiency_days_post_occupancy} days post-occupancy
              </td>
            </tr>
          </tbody>
        </table>
        )}
      </section>

      {months.length > 0 && (
        <section className={card}>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700 mb-1">Money in and out, by month</h2>
          <p className="text-xs text-slate-500 mb-3">In: contract stage payments. Out: bills entered plus the assumed cost of the work still to do. Before GST.</p>
          <div className="flex gap-3 overflow-x-auto pb-1">
            {months.map((m) => {
              const label = new Date(`${m.month}-15T12:00:00Z`).toLocaleDateString("en-CA", { month: "short", year: "2-digit", timeZone: "UTC" });
              return (
                <div key={m.month} className="flex min-w-[88px] flex-1 flex-col gap-1">
                  <div className="flex h-36 items-end gap-1.5 border-b border-slate-400 px-1">
                    <div className="flex-1 rounded-t bg-slate-900" style={{ height: `${(m.in / peak) * 100}%` }} title={`In ${fmtMoney(m.in)}`} />
                    <div className="flex-1 rounded-t bg-slate-300" style={{ height: `${(m.out / peak) * 100}%` }} title={`Out ${fmtMoney(m.out)}`} />
                  </div>
                  <b className="text-xs">{label}</b>
                  <span className="text-[11px] text-slate-600 tabular-nums">in {fmtMoney(m.in)}<br />out {fmtMoney(m.out)}</span>
                </div>
              );
            })}
          </div>
          <div className="mt-2 flex gap-4 text-xs text-slate-600">
            <span><span className="mr-1 inline-block h-3 w-3 bg-slate-900 align-middle" />in</span>
            <span><span className="mr-1 inline-block h-3 w-3 bg-slate-300 align-middle" />out</span>
          </div>
        </section>
      )}

      <section className={card}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700 mb-2">
          Cumulative cash position
        </h2>
        <CashPositionChart
          periods={cf.weekly_periods.map((w) => ({
            week_num: w.week_num,
            week_end: w.week_end,
            cumulative_position: w.cumulative_position,
          }))}
        />
        <p className="text-xs text-slate-500 mt-1">
          Below the dashed zero line, BRIO is funding the project out of pocket —
          the trough is the peak bridge financing required. Hover for any week.
        </p>
      </section>

      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700 mb-2">
          Weekly cash flow ({cf.weekly_periods.length} weeks)
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-xs border border-slate-200">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-2 py-1 text-left">Wk</th>
                <th className="px-2 py-1 text-left">W/E</th>
                <th className="px-2 py-1 text-right">Draws in</th>
                <th className="px-2 py-1 text-right">Outflow</th>
                <th className="px-2 py-1 text-right">Net</th>
                <th className="px-2 py-1 text-right">Cumulative</th>
                <th className="px-2 py-1 text-left">Milestones</th>
              </tr>
            </thead>
            <tbody>
              {cf.weekly_periods.map((w) => {
                const pos = w.cumulative_position ?? 0;
                const cumClass =
                  pos < -100000 ? "text-red-700 font-medium"
                  : pos < 0 ? "text-amber-700"
                  : "text-emerald-700";
                return (
                  <tr key={w.week_num} className="border-t border-slate-100">
                    <td className="px-2 py-1 text-slate-500">{w.week_num}</td>
                    <td className="px-2 py-1 text-slate-700 whitespace-nowrap">{fmtDate(w.week_end)}</td>
                    <td className="px-2 py-1 text-right">{fmtMoney(w.draws_total)}</td>
                    <td className="px-2 py-1 text-right text-slate-500">{fmtMoney(w.outflow_total)}</td>
                    <td className="px-2 py-1 text-right">{fmtMoney(w.net_this_week)}</td>
                    <td className={`px-2 py-1 text-right ${cumClass}`}>{fmtMoney(pos)}</td>
                    <td className="px-2 py-1 text-xs text-slate-600">
                      {w.draws.map((dr) => dr.milestone).join("; ")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

    </div>
  );
}
