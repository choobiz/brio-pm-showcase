import Link from "next/link";
import { loadOr } from "@/lib/loadOr";
import { LoadFailed } from "@/components/LoadFailed";
import { listSovs, getSov, getSovProgress, fmtMoney } from "@/lib/workbench";
import { StaleBanner } from "../StaleBanner";
import { CashSovSwitch, NotBuiltYet } from "../CashSovSwitch";

export default async function SovPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ scenario?: string }>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const scenario = sp.scenario === "combined" ? "combined" : "base";

  const res = await loadOr("sov", listSovs(id));
  if (res.failed) {
    return (
      <div className="space-y-4">
        <CashSovSwitch projectId={id} on="sov" />
        <LoadFailed what="the schedule of values" />
      </div>
    );
  }
  const all = res.data ?? [];
  const chosen = all.find((s) => s.scenario === scenario);

  if (!chosen) {
    return (
      <div className="space-y-4">
        <CashSovSwitch projectId={id} on="sov" />
        <NotBuiltYet projectId={id} what="schedule of values" />
      </div>
    );
  }

  const sov = await getSov(id, chosen.id);
  const prog = await loadOr("sov progress", getSovProgress(id));
  const pr = prog.data?.progress ?? null;
  const pc = (n: number | null) => (n == null ? "—" : `${Math.round(n)}%`);
  const hasOption = all.some((s) => s.scenario === "combined");
  // Built by Contract › Billing plan: rows are the Costs budget, and the
  // holdback is its own contract stage, not 10% off each month.
  const fromPlan = sov.rows.some((r) => (r as { basis?: string }).basis === "budget");
  const tabActive = "px-3 py-1.5 rounded bg-slate-900 text-white text-xs font-medium";
  const tabIdle = "px-3 py-1.5 rounded bg-slate-100 text-slate-700 text-xs hover:bg-slate-200";

  return (
    <div className="space-y-5">
      <CashSovSwitch projectId={id} on="sov" />
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-xl font-semibold">Schedule of Values</h1>
          <p className="text-sm text-slate-600 mt-1">
            Version {sov.version_number}
            {hasOption && <> · Scenario: <span className="font-medium uppercase">{scenario}</span></>} · Total{" "}
            <span className="font-medium">{fmtMoney(sov.total_pretax)}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          {hasOption && <><Link
            href={`/projects/${id}/workbench/sov?scenario=base`}
            className={scenario === "base" ? tabActive : tabIdle}
          >
            Base
          </Link>
          <Link
            href={`/projects/${id}/workbench/sov?scenario=combined`}
            className={scenario === "combined" ? tabActive : tabIdle}
          >
            Base + Option
          </Link></>}
          <a
            href={`/api/workbench/sov-csv/${id}?scenario=${scenario}`}
            className="ml-1 inline-flex items-center gap-1.5 rounded-md bg-[var(--color-brio-primary)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
            title="Download this Schedule of Values as a CSV (G703-style) for draw submissions"
          >
            ⬇ Export CSV
          </a>
        </div>
      </header>

      {fromPlan && sov.notes && <p className="text-xs text-slate-500 max-w-3xl">{sov.notes} The holdback is its own contract stage.</p>}

      <StaleBanner
        projectId={id}
        basedOnEstimateId={sov.based_on_estimate_id}
        isStale={sov.is_stale}
      />

      {prog.failed && <LoadFailed what="progress" small />}
      {pr && (
        <section className="rounded border border-slate-200 bg-white">
          <div className="flex flex-wrap items-baseline justify-between gap-2 px-4 pt-3">
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">Progress · as of {pr.as_of}</h2>
              <p className="text-xs text-slate-600">% done comes from Schedule › Record what happened. Billed is what has been invoiced for contract stages, shown against each line by its share of the contract. Before GST.</p>
            </div>
            <a href={`/api/backend/projects/${id}/billing-plan/sov.pdf`} target="_blank" rel="noopener noreferrer"
              className="rounded border border-slate-900 px-3 py-1.5 text-xs font-medium">PDF for a lender or the client</a>
          </div>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
                <tr><th className="px-3 py-2 text-left">Line</th><th className="px-3 py-2 text-right">Value</th><th className="px-3 py-2 text-right">% done</th>
                  <th className="px-3 py-2 text-right">Earned</th><th className="px-3 py-2 text-right">Billed</th><th className="px-3 py-2 text-right">Left</th></tr>
              </thead>
              <tbody>
                {pr.rows.map((r) => (
                  <tr key={r.line} className="border-t border-slate-100">
                    <td className="px-3 py-1.5"><span className="text-xs text-slate-500">{r.line}</span> {r.description}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{fmtMoney(r.value)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{pc(r.pct_done)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{fmtMoney(r.earned)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{fmtMoney(r.billed)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{fmtMoney(r.left)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t border-slate-300 font-semibold">
                <tr><td className="px-3 py-2">Total</td><td className="px-3 py-2 text-right">{fmtMoney(pr.total.value)}</td>
                  <td className="px-3 py-2 text-right">{pc(pr.total.pct_done)}</td><td className="px-3 py-2 text-right">{fmtMoney(pr.total.earned)}</td>
                  <td className="px-3 py-2 text-right">{fmtMoney(pr.total.billed)}</td><td className="px-3 py-2 text-right">{fmtMoney(pr.total.left)}</td></tr>
              </tfoot>
            </table>
          </div>
          <p className="px-4 py-2 text-xs text-slate-600">
            {pr.total.billed_ahead > 0
              ? `Billed ${fmtMoney(pr.total.billed_ahead)} ahead of the work recorded as done.`
              : pr.total.billed_ahead < 0 ? `Work recorded as done is ${fmtMoney(-pr.total.billed_ahead)} ahead of billing.` : "Billing matches the work recorded."}
            {pr.pct_note && ` ${pr.pct_note}`}
          </p>
        </section>
      )}

      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">Planned by month</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-xs border border-slate-200">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-2 py-1 text-left">Trade #</th>
              <th className="px-2 py-1 text-left">Description</th>
              <th className="px-2 py-1 text-right">Scheduled Value</th>
              {sov.months.map((m) => (
                <th key={m} className="px-2 py-1 text-right">{m}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sov.rows.map((r) => (
              <tr key={r.tradeNumber} className={`border-t border-slate-100 ${r.optional ? "bg-amber-50" : ""}`}>
                <td className="px-2 py-1 font-medium">{r.tradeNumber}</td>
                <td className="px-2 py-1">
                  {r.description}
                  {r.optional && <span className="ml-1 text-amber-700 font-medium">(OPT)</span>}
                </td>
                <td className="px-2 py-1 text-right font-medium">{fmtMoney(r.scheduledValue)}</td>
                {sov.months.map((m) => (
                  <td key={m} className="px-2 py-1 text-right">
                    {fmtMoney(r.monthly[m])}
                    <div className="text-[9px] text-slate-500">{r.cumulativePct[m]}%</div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          <tfoot className="bg-slate-100 font-medium">
            <tr>
              <td colSpan={3} className="px-2 py-2 text-right">Monthly draw (gross)</td>
              {sov.months.map((m) => (
                <td key={m} className="px-2 py-2 text-right">{fmtMoney(sov.monthly_totals[m])}</td>
              ))}
            </tr>
            <tr>
              <td colSpan={3} className="px-2 py-2 text-right text-slate-600">Cumulative</td>
              {sov.months.map((m) => (
                <td key={m} className="px-2 py-2 text-right text-slate-600">
                  {fmtMoney(sov.cumulative_totals[m])}
                </td>
              ))}
            </tr>
            {!fromPlan && <>
            <tr>
              <td colSpan={3} className="px-2 py-2 text-right text-amber-700">Lien holdback 10%</td>
              {sov.months.map((m) => (
                <td key={m} className="px-2 py-2 text-right text-amber-700">
                  {fmtMoney(sov.lien_holdback_10pct[m])}
                </td>
              ))}
            </tr>
            <tr>
              <td colSpan={3} className="px-2 py-2 text-right text-emerald-700">Owner net to BRIO (90%)</td>
              {sov.months.map((m) => (
                <td key={m} className="px-2 py-2 text-right text-emerald-700">
                  {fmtMoney(sov.owner_net_monthly[m])}
                </td>
              ))}
            </tr>
            </>}
          </tfoot>
        </table>
      </div>
    </div>
  );
}
