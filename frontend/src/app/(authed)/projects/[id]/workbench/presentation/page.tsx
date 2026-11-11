import Link from "next/link";
import { loadOr } from "@/lib/loadOr";
import { LoadFailed } from "@/components/LoadFailed";

import { listPresentations, listEstimateVersions, getPriorClientEstimate, fmtDate } from "@/lib/workbench";
import { GeneratePresentationButton } from "./GeneratePresentationButton";
import { PriorEstimateForm } from "./PriorEstimateForm";
import { PortalPanel } from "./PortalPanel";

export default async function PresentationIndex({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [pr, est, prior] = await Promise.all([
    loadOr("presentations", listPresentations(id)),
    loadOr("estimate versions", listEstimateVersions(id)),
    getPriorClientEstimate(id).catch(() => ({ prior_client_estimate: null })),
  ]);
  if (pr.failed || est.failed) return <LoadFailed what="the proposals" />;
  const presentations = pr.data ?? [];
  const estimates = est.data ?? [];
  const hasEstimate = estimates.length > 0;
  const activeEstId = estimates.find((e) => e.is_active)?.id ?? null;

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold">Proposal</h1>
        <p className="mt-1 text-sm text-slate-600">
          A client-facing deck assembled from the active estimate, scope, schedule &amp; cash flow —
          with the budget, high-cost items explained, and the early decisions you need from the client.
        </p>
      </header>

      {!hasEstimate && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          This project has no estimate yet. Generate an estimate first, then come back to build the presentation.
        </div>
      )}

      <PriorEstimateForm projectId={id} initial={prior.prior_client_estimate} />

      <GeneratePresentationButton projectId={id} hasEstimate={hasEstimate} />

      <PortalPanel projectId={id} />

      {presentations.length === 0 ? (
        <p className="text-sm text-slate-500">No presentations yet.</p>
      ) : (
        <table className="w-full border border-slate-200 text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2">Version</th>
              <th className="px-3 py-2">Label</th>
              <th className="px-3 py-2">Audience</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Created</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {presentations.map((p) => {
              const stale =
                !!activeEstId && !!p.based_on_estimate_id && p.based_on_estimate_id !== activeEstId;
              return (
                <tr key={p.id} className="border-t border-slate-100">
                  <td className="px-3 py-2 font-medium">v{p.version_number}</td>
                  <td className="px-3 py-2 text-slate-700">{p.label ?? "—"}</td>
                  <td className="px-3 py-2 capitalize text-slate-600">{p.audience}</td>
                  <td className="px-3 py-2">
                    <span className="capitalize text-slate-600">{p.status}</span>
                    {stale && (
                      <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800">
                        estimate changed since
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-slate-500">{fmtDate(p.created_at)}</td>
                  <td className="px-3 py-2 text-right">
                    <Link
                      href={`/projects/${id}/workbench/presentation/${p.id}`}
                      className="font-medium"
                      style={{ color: "#0068A0" }}
                    >
                      Open →
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
