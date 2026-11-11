import Link from "next/link";
import { loadOr } from "@/lib/loadOr";
import { LoadFailed } from "@/components/LoadFailed";
import { getWorkbenchOverview, listEstimateVersions } from "@/lib/workbench";
import { RegenerateButton } from "./RegenerateButton";

/**
 * Phase 3a banner — surfaces the "based on vNNN" relationship for any
 * downstream artifact (SOV, cash flow, contract) and flags it as stale when
 * the artifact's based_on_estimate_id doesn't match the project's active
 * estimate. Always renders so the user can see the anchoring relationship
 * even when up-to-date.
 *
 * Server component — pulls overview + estimate list once and resolves the
 * basis estimate label by id.
 */
export async function StaleBanner({
  projectId,
  basedOnEstimateId,
  isStale,
}: {
  projectId: string;
  basedOnEstimateId: string | null;
  isStale: boolean;
}) {
  const [ov, est] = await Promise.all([
    loadOr("workbench overview", getWorkbenchOverview(projectId)),
    loadOr("estimate versions", listEstimateVersions(projectId)),
  ]);
  if (ov.failed || est.failed) return <LoadFailed what="which estimate this is based on" small />;
  const overview = ov.data;
  const estimates = est.data ?? [];
  const active = overview?.active_estimate ?? null;
  const basis = estimates.find((e) => e.id === basedOnEstimateId) ?? null;

  if (!basis && !active) return null;

  const tone = isStale
    ? "border-amber-300 bg-amber-50 text-amber-900"
    : "border-emerald-200 bg-emerald-50/50 text-emerald-900";

  return (
    <div className={`rounded border ${tone} px-3 py-2 text-sm flex items-baseline justify-between`}>
      <div>
        <span className="font-semibold">Based on:</span>{" "}
        {basis ? (
          <Link
            href={`/projects/${projectId}/estimate/${basis.id}`}
            className="underline decoration-dotted hover:decoration-solid"
          >
            v{String(basis.version_number).padStart(3, "0")} — {basis.reference}
          </Link>
        ) : (
          <span className="italic">no linked estimate</span>
        )}
        {isStale && active && (
          <span className="ml-2">
            (active is{" "}
            <Link
              href={`/projects/${projectId}/estimate/${active.id}`}
              className="underline decoration-dotted hover:decoration-solid"
            >
              v{String(active.version_number).padStart(3, "0")}
            </Link>
            )
          </span>
        )}
      </div>
      <div className="text-xs uppercase tracking-wide font-semibold flex items-center">
        {isStale ? "⚠ Out of sync" : "✓ Up to date"}
        {isStale && active && <RegenerateButton projectId={projectId} />}
      </div>
    </div>
  );
}
