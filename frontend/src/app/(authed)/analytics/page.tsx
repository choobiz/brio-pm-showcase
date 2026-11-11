"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Card } from "@/components/ui/card";
import {
  getCostAnalytics,
  getVarianceAnalytics,
  type CostAnalytics,
  type VarianceAnalytics,
} from "@/lib/analytics";

const money = (n: number) =>
  n.toLocaleString("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 });

const KIND_LABEL: Record<string, string> = {
  subtrade_invoice: "Subtrade invoice",
  supplier_invoice: "Supplier invoice",
  subtrade_quote: "Subtrade quote",
  other: "Other",
};

export default function AnalyticsPage() {
  const [data, setData] = useState<CostAnalytics | null>(null);
  const [variance, setVariance] = useState<VarianceAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getCostAnalytics(), getVarianceAnalytics()])
      .then(([c, v]) => {
        setData(c);
        setVariance(v);
      })
      .catch((e) => setError(String(e)));
  }, []);

  if (error) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        {error}
      </div>
    );
  }
  if (!data) {
    return <div className="text-slate-400">Loading…</div>;
  }

  const { summary, by_category, by_project, by_year, by_kind } = data;
  const catMax = Math.max(1, ...by_category.map((c) => c.total));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>
          Cost Intelligence
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          What BRIO&apos;s work actually costs — subtrade &amp; supplier actuals
          from {summary.projects_with_costs} projects.
        </p>
      </div>

      {/* Summary tiles */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          ["Total recorded cost", money(summary.total_amount)],
          ["Cost records", String(summary.cost_count)],
          ["Projects with costs", String(summary.projects_with_costs)],
          ["Categories covered", String(summary.category_count)],
        ].map(([label, value]) => (
          <Card key={label} className="p-4">
            <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
            <div
              className="mt-1 text-2xl font-semibold"
              style={{ color: "var(--color-brio-primary)" }}
            >
              {value}
            </div>
          </Card>
        ))}
      </div>

      {/* Cost by category — bar chart */}
      <Card className="p-6">
        <h2 className="mb-4 font-semibold text-slate-800">Cost by category</h2>
        {by_category.length === 0 ? (
          <p className="text-sm text-slate-400">No cost data.</p>
        ) : (
          <div className="space-y-2">
            {by_category.map((c) => (
              <div key={c.category} className="flex items-center gap-3 text-sm">
                <div className="w-52 shrink-0 truncate text-slate-600" title={c.category}>
                  {c.category}
                </div>
                <div className="flex-1 bg-slate-100">
                  <div
                    className="h-5"
                    style={{
                      width: `${(c.total / catMax) * 100}%`,
                      backgroundColor: "var(--color-brio-primary)",
                      minWidth: "2px",
                    }}
                  />
                </div>
                <div className="w-28 shrink-0 text-right font-medium text-slate-700">
                  {money(c.total)}
                </div>
                <div className="w-24 shrink-0 text-right text-xs text-slate-400">
                  {c.count} · ~{money(c.avg)}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Estimate vs. actual — projects with both */}
      <Card className="p-0 overflow-hidden">
        <h2 className="border-b border-slate-200 px-6 py-3 font-semibold text-slate-800">
          Estimate vs. actual{" "}
          <span className="text-xs font-normal text-slate-400">
            (projects with both an estimate and recorded costs)
          </span>
        </h2>
        {!variance || variance.projects.length === 0 ? (
          <p className="px-6 py-6 text-sm text-slate-400">
            No projects have both an estimate and recorded costs yet.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="px-6 py-2 font-medium">Project</th>
                <th className="px-6 py-2 text-right font-medium">Estimate (base)</th>
                <th className="px-6 py-2 text-right font-medium">Actual</th>
                <th className="px-6 py-2 text-right font-medium">Delta</th>
              </tr>
            </thead>
            <tbody>
              {variance.projects.map((p) => {
                const over = p.delta > 0;
                return (
                  <tr
                    key={p.project_id}
                    className="border-b border-slate-100 hover:bg-slate-50"
                  >
                    <td className="px-6 py-2.5">
                      <Link
                        href={`/projects/${p.project_id}/overview`}
                        className="text-[var(--color-brio-primary)] hover:underline"
                      >
                        {p.name}
                      </Link>
                    </td>
                    <td className="px-6 py-2.5 text-right text-slate-600">
                      {money(p.estimate.subtotal)}
                    </td>
                    <td className="px-6 py-2.5 text-right text-slate-600">
                      {money(p.actual.total)}
                    </td>
                    <td
                      className="px-6 py-2.5 text-right font-medium"
                      style={{ color: over ? "#b91c1c" : "#047857" }}
                    >
                      {over ? "+" : ""}
                      {money(p.delta)}
                      {p.pct != null && (
                        <span className="ml-1 text-xs text-slate-400">
                          ({over ? "+" : ""}
                          {p.pct.toFixed(0)}%)
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* By project */}
        <Card className="p-0 overflow-hidden">
          <h2 className="border-b border-slate-200 px-6 py-3 font-semibold text-slate-800">
            Cost by project
          </h2>
          <table className="w-full text-sm">
            <tbody>
              {by_project.map((p) => (
                <tr key={p.project_id} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="px-6 py-2.5">
                    <Link
                      href={`/projects/${p.project_id}/overview`}
                      className="text-[var(--color-brio-primary)] hover:underline"
                    >
                      {p.name}
                    </Link>
                  </td>
                  <td className="px-6 py-2.5 text-right text-xs text-slate-400">
                    {p.count}
                  </td>
                  <td className="px-6 py-2.5 text-right font-medium text-slate-700">
                    {money(p.total)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        {/* By year + by kind */}
        <div className="space-y-6">
          <Card className="p-6">
            <h2 className="mb-3 font-semibold text-slate-800">Cost by year</h2>
            <div className="space-y-1.5 text-sm">
              {by_year.map((y) => (
                <div key={String(y.year)} className="flex justify-between">
                  <span className="text-slate-600">{y.year ?? "Undated"}</span>
                  <span className="font-medium text-slate-700">{money(y.total)}</span>
                </div>
              ))}
            </div>
          </Card>
          <Card className="p-6">
            <h2 className="mb-3 font-semibold text-slate-800">Cost by source</h2>
            <div className="space-y-1.5 text-sm">
              {by_kind.map((k) => (
                <div key={k.kind} className="flex justify-between">
                  <span className="text-slate-600">{KIND_LABEL[k.kind] ?? k.kind}</span>
                  <span className="font-medium text-slate-700">
                    {money(k.total)}{" "}
                    <span className="text-xs text-slate-400">({k.count})</span>
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
