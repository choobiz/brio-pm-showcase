"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  getBillingPlan,
  previewBillingPlan,
  saveBillingPlan,
  type BillingPlan as Plan,
  type PlanInput,
} from "@/lib/billingPlan";
import { errorMessage } from "@/lib/workspaceApi";

/**
 * Contract › Billing plan (round 2, approved 2026-11-02). The signed stages
 * as they are, when each is billed (an invoice already issued, else the finish
 * of a schedule activity picked here, from the LATEST schedule), and saving
 * builds the schedule of values and the cash flow. Nothing signed changes.
 */

const money = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("en-CA", { style: "currency", currency: "CAD" });

/** "2026-12-03" → "Thu, Dec 3" without the UTC-midnight day slip. */
function d(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, day] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, day)).toLocaleDateString("en-CA", {
    weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
  });
}

function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b.slice(0, 10)) - Date.parse(a.slice(0, 10))) / 86400000);
}

export function BillingPlan({ projectId }: { projectId: string }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [edits, setEdits] = useState<PlanInput>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<Plan["saved"] | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    getBillingPlan(projectId).then(setPlan, (e) => setLoadErr(errorMessage(e)));
  }, [projectId]);

  // Every pick is previewed by the server, so dates and problems are its own.
  const change = useCallback((next: PlanInput) => {
    setEdits(next);
    setSaved(null);
    const n = ++seq.current;
    previewBillingPlan(projectId, next).then(
      (p) => { if (n === seq.current) { setPlan(p); setErr(null); } },
      (e) => { if (n === seq.current) setErr(errorMessage(e)); },
    );
  }, [projectId]);

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const p = await saveBillingPlan(projectId, edits);
      setPlan(p);
      setSaved(p.saved ?? null);
      setEdits({});
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const card = "rounded border border-slate-200 bg-white";
  if (loadErr) {
    return (
      <section className={`${card} p-4 text-sm`}>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700 mb-1">Billing plan</h2>
        <p className="text-slate-700">{loadErr}</p>
      </section>
    );
  }
  if (!plan) return <section className={`${card} p-4 text-sm text-slate-500`}>Loading the billing plan…</section>;

  const acts = plan.activities.filter((a) => a.finish_date).sort((x, y) => x.finish_date.localeCompare(y.finish_date));
  const sc = plan.contract.substantial_completion_actual;
  const expected = plan.schedule.expected_substantial_completion;
  const late = plan.contract.substantial_completion_date && expected
    ? daysBetween(plan.contract.substantial_completion_date, sc ?? expected) : 0;
  const th = "px-3 py-2 text-left";

  return (
    <section className={card}>
      <div className="flex flex-wrap items-baseline justify-between gap-2 px-4 pt-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">Billing plan</h2>
        <span className="text-xs text-slate-500">
          From the signed contract ({plan.contract.reference}, {plan.contract.status}). Dates from schedule
          v{plan.schedule.version_number} (latest)
          {plan.schedule.client_version != null && plan.schedule.client_version !== plan.schedule.version_number
            && <>; the client sees v{plan.schedule.client_version}</>}.
        </span>
      </div>

      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
            <tr>
              <th className={th}>Stage</th>
              <th className="px-3 py-2 text-right">%</th>
              <th className="px-3 py-2 text-right">Before GST</th>
              <th className={th}>Billed when…</th>
              <th className={th}>Expected</th>
              <th className={th}>Status</th>
            </tr>
          </thead>
          <tbody>
            {plan.stages.map((s) => (
              <tr key={s.no} className="border-t border-slate-100 align-top">
                <td className="px-3 py-2">{s.milestone}</td>
                <td className="px-3 py-2 text-right">{s.pct}</td>
                <td className="px-3 py-2 text-right whitespace-nowrap">{money(s.amount)}</td>
                <td className="px-3 py-2">
                  {s.invoice ? (
                    <span className="text-slate-600">invoiced</span>
                  ) : s.holdback ? (
                    <span className="text-slate-600">substantial completion</span>
                  ) : (
                    <select
                      value={s.activity_code ?? ""}
                      onChange={(e) => change({
                        ...edits,
                        stage_activities: { ...(edits.stage_activities ?? {}), [String(s.no)]: e.target.value || null },
                      })}
                      className={`max-w-[18rem] rounded border px-2 py-1 text-sm ${s.needs_pick ? "border-orange-400 bg-orange-50" : "border-slate-300"}`}
                    >
                      <option value="">Pick an activity…</option>
                      {acts.map((a) => (
                        <option key={a.code} value={a.code}>
                          {a.code} {a.name} · {d(a.finish_date)}
                        </option>
                      ))}
                    </select>
                  )}
                </td>
                <td className="px-3 py-2 whitespace-nowrap">{d(s.bill_date)}</td>
                <td className="px-3 py-2 text-xs">
                  {s.invoice ? (
                    <>{s.invoice.number} · {s.invoice.status}
                      {s.invoice.paid_at && <> {d(s.invoice.paid_at)}</>}</>
                  ) : s.holdback ? (
                    <>not billed · paid {plan.holdback_release_days} days after, {d(s.paid_date)}</>
                  ) : "not billed"}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-slate-200 font-medium">
              <td className="px-3 py-2">Total</td>
              <td />
              <td className="px-3 py-2 text-right whitespace-nowrap">{money(plan.stage_total)}</td>
              <td colSpan={3} className="px-3 py-2 text-xs font-normal text-amber-800">
                {plan.stages_off_by ? (
                  <>The signed stages add up to {money(Math.abs(plan.stages_off_by))} {plan.stages_off_by > 0 ? "more" : "less"} than
                    the contract with approved change orders ({money(plan.contract.revised)}). Left as signed.</>
                ) : null}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <div className="grid gap-4 border-t border-slate-100 px-4 py-3 text-sm md:grid-cols-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-600">Substantial completion</div>
          <div className="mt-1">
            In the contract {d(plan.contract.substantial_completion_date)} · expected {d(expected)} (v{plan.schedule.version_number} P50)
            {late > 0 && <span className="text-amber-800"> · {late} days after the contract date</span>}
          </div>
          <label className="mt-1 flex flex-wrap items-center gap-2">
            Actual
            <input
              type="date"
              value={(edits.substantial_completion_actual !== undefined ? edits.substantial_completion_actual : sc) ?? ""}
              max={new Date().toLocaleDateString("en-CA", { timeZone: "America/Vancouver" })}
              onChange={(e) => change({ ...edits, substantial_completion_actual: e.target.value || null })}
              className="rounded border border-slate-300 px-2 py-1 text-sm"
            />
            <span className="text-xs text-slate-500">record it when it happens; the holdback is due {plan.holdback_release_days} days later</span>
          </label>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-600">Cash flow assumptions</div>
          <label className="mt-1 flex flex-wrap items-center gap-2">
            Cost assumed =
            <input
              type="number" min={1} max={100} step={1}
              value={edits.cost_share_pct ?? plan.cost_share_pct}
              onChange={(e) => change({ ...edits, cost_share_pct: Number(e.target.value) })}
              className="w-20 rounded border border-slate-300 px-2 py-1 text-sm"
            />
            % of the budget still to spend
          </label>
          <label className="mt-1 flex flex-wrap items-center gap-2">
            Client pays
            <input
              type="number" min={0} max={120} step={1}
              value={edits.owner_net_days ?? plan.owner_net_days}
              onChange={(e) => change({ ...edits, owner_net_days: Number(e.target.value) })}
              className="w-20 rounded border border-slate-300 px-2 py-1 text-sm"
            />
            days after billing
          </label>
        </div>
      </div>

      {plan.divisions.length > 0 && (
        <details className="border-t border-slate-100" open={plan.divisions.some((x) => x.needs_pick)}>
          <summary className="cursor-pointer px-4 py-2 text-sm font-semibold text-slate-700">
            Schedule of values · which schedule work each budget division is spread over
          </summary>
          <table className="w-full text-sm">
            <tbody>
              {plan.divisions.map((x) => (
                <tr key={x.division} className="border-t border-slate-100">
                  <td className="px-4 py-1.5">{x.division}</td>
                  <td className="px-3 py-1.5 text-right whitespace-nowrap">{money(x.budget)}</td>
                  <td className="px-3 py-1.5">
                    {x.whole_job ? (
                      <span className="text-slate-600">the whole job</span>
                    ) : (
                      <select
                        value={x.picked ?? (x.phases.length === 1 ? x.phases[0] : "")}
                        onChange={(e) => change({
                          ...edits,
                          division_phases: { ...(edits.division_phases ?? {}), [x.division]: e.target.value || null },
                        })}
                        className={`rounded border px-2 py-1 text-sm ${x.needs_pick ? "border-orange-400 bg-orange-50" : "border-slate-300"}`}
                      >
                        <option value="">{x.phases.length > 1 ? x.phases.join(" + ") : "Pick schedule work…"}</option>
                        {plan.phases.map((p) => <option key={p} value={p}>{p}</option>)}
                      </select>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 bg-slate-50 px-4 py-3">
        <span className="text-xs text-slate-600">
          {plan.problems.length > 0
            ? plan.problems.join(" ")
            : "Saving builds a new schedule of values and cash flow; earlier versions stay. The signed contract doesn't change."}
        </span>
        <button
          type="button" onClick={save} disabled={busy || !plan.complete}
          className="ml-auto rounded bg-[var(--color-brio-primary)] px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? "Saving…" : "Save the plan"}
        </button>
      </div>
      {err && <div className="border-t border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800">{err}</div>}
      {saved && (
        <div className="border-t border-green-700 bg-white px-4 py-3 text-sm" role="status">
          <span className="font-semibold text-green-800">Saved.</span>{" "}
          Schedule of values v{saved.sov.version_number} and cash flow v{saved.cash_flow.version_number} built.
          {saved.cash_flow.bridge_financing_required > 0
            ? <> Lowest cash point {money(saved.cash_flow.min_cash_position)} in the week ending {d(saved.cash_flow.min_position_week_end)}.</>
            : <> Cash never goes below zero.</>}{" "}
          <Link href={`/projects/${projectId}/workbench/sov`} className="text-blue-700">Schedule of values</Link>
          {" · "}
          <Link href={`/projects/${projectId}/workbench/cashflow`} className="text-blue-700">Cash flow</Link>
        </div>
      )}
    </section>
  );
}
