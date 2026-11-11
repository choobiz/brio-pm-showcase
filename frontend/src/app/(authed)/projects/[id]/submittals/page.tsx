"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";

import { listSubmittals, STATUS_LABEL, type SubmittalRow } from "@/lib/submittals";

type Filter = "all" | "needs_call" | "waiting" | "closed";

/**
 * Submittals (wireframe A, approved by Amir 2026-10-17): shop drawings, product
 * data and quotes, checked against the project's documents before anything is
 * ordered or built. One row per submittal, its newest revision.
 */
export default function SubmittalsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [rows, setRows] = useState<SubmittalRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    listSubmittals(id)
      .then(setRows)
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [id]);

  const current = useMemo(() => (rows ?? []).filter((r) => r.status !== "superseded"), [rows]);
  const count = (f: Filter) => (f === "all" ? current.length : current.filter((r) => r.status === f).length);
  const shown = filter === "all" ? current : current.filter((r) => r.status === filter);
  const chips: { key: Filter; label: string }[] = [
    { key: "all", label: "All" },
    { key: "needs_call", label: "Needs your call" },
    { key: "waiting", label: "Waiting on others" },
    { key: "closed", label: "Closed" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="m-0 text-2xl font-semibold">Submittals</h1>
          <p className="m-0 text-sm text-slate-600">
            Shop drawings, product data and quotes, checked against the drawings and engineers&apos; plans before anything is
            ordered or built.
          </p>
        </div>
        <Link href={`/projects/${id}/submittals/new`} className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white">
          Review a submittal
        </Link>
      </div>

      <div className="flex flex-wrap gap-2 text-sm">
        {chips.map((c) => (
          <button
            key={c.key}
            type="button"
            onClick={() => setFilter(c.key)}
            className={`rounded-full border px-3 py-1 ${filter === c.key ? "border-slate-900 bg-white" : "border-slate-300"}`}
          >
            {c.label} {count(c.key)}
          </button>
        ))}
      </div>

      {err && <p className="text-sm text-red-700">{err}</p>}
      {rows === null && !err && <p className="text-sm text-slate-500">Loading…</p>}
      {rows !== null && current.length === 0 && (
        <p className="rounded-md border border-dashed border-slate-300 p-6 text-sm text-slate-600">
          No submittals yet. When a shop drawing or quote comes in, review it here before it&apos;s ordered.
        </p>
      )}

      {shown.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
          <table className="w-full min-w-[860px] text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-2 font-semibold">Submittal</th>
                <th className="px-3 py-2 font-semibold">Trade</th>
                <th className="px-3 py-2 font-semibold">Rev</th>
                <th className="px-3 py-2 font-semibold">Checked against</th>
                <th className="px-3 py-2 font-semibold">Findings</th>
                <th className="px-3 py-2 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.id} className="border-t border-slate-100 align-top">
                  <td className="px-4 py-3">
                    <Link href={`/projects/${id}/submittals/${r.id}`} className="font-semibold text-slate-900 hover:underline">
                      {r.name}
                    </Link>
                    <div className="text-xs text-slate-500">
                      {r.ref}
                      {r.supplier ? ` · ${r.supplier}` : ""}
                      {r.created_at ? ` · received ${new Date(r.created_at).toLocaleDateString("en-CA", { month: "short", day: "numeric" })}` : ""}
                    </div>
                  </td>
                  <td className="px-3 py-3">{r.trade_label}</td>
                  <td className="px-3 py-3">{r.revision}</td>
                  <td className="px-3 py-3 text-xs">{r.checked_against.join(", ")}</td>
                  <td className="px-3 py-3">
                    <Counts row={r} />
                  </td>
                  <td className="px-3 py-3">
                    <div className="font-semibold">{r.outcome_label ?? STATUS_LABEL[r.status]}</div>
                    {r.cleared && <div className="text-xs text-emerald-800">Cleared to order</div>}
                    {r.milestone && (
                      <div className="text-xs text-slate-500">
                        Tied to {r.milestone.ref} {r.milestone.title}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Counts({ row }: { row: SubmittalRow }) {
  if (row.status === "reviewing" || row.status === "draft") return <span className="text-slate-500">—</span>;
  if (row.status === "review_failed") return <span className="text-red-700">Didn&apos;t finish</span>;
  const c = row.counts;
  const open = c.critical + c.confirm + c.not_shown;
  if (!open) return <span className="rounded bg-emerald-50 px-2 py-0.5 text-xs text-emerald-900">all match</span>;
  return (
    <span className="flex flex-wrap gap-1.5 text-xs">
      {c.critical > 0 && <span className="rounded bg-red-900 px-2 py-0.5 text-white">{c.critical} critical</span>}
      {c.confirm + c.not_shown > 0 && (
        <span className="rounded bg-orange-100 px-2 py-0.5 text-orange-900">{c.confirm + c.not_shown} confirm</span>
      )}
    </span>
  );
}
