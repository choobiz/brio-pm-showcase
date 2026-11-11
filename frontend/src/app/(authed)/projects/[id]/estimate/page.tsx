import Link from "next/link";
import { loadOr } from "@/lib/loadOr";
import { LoadFailed } from "@/components/LoadFailed";

import { Box, Label } from "@/components/kit";
import { api } from "@/lib/api";
import { fmtDate, fmtMoney } from "@/lib/fmt";
import { getEstimateReadiness, listEstimateVersions, listScopes } from "@/lib/workbench";

import { GenerateEstimateButtons } from "../workbench/estimates/GenerateEstimateButtons";
import { SetActiveButton } from "../workbench/estimates/SetActiveButton";
import { BeforeNextVersion, ChangeByDescribing, DiscardDraftButton } from "./EstimateIslands";

/**
 * Estimate — round-1 wireframe. The current version and how solid it is, what
 * to settle before the next one, making a new one, and every version with its
 * total. Totals are the server's (`totals`), the same arithmetic as Home.
 *
 * "Draft" is any version that is not active: an AI run, or a described change.
 * Keep = make it the current one (with the regeneration preview, as before);
 * Discard = delete it, refused if anything was built from it.
 */
export default async function EstimatePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [versionsRes, readiness, divisions] = await Promise.all([
    loadOr("estimate versions", listEstimateVersions(id)),
    getEstimateReadiness(id).catch(() => null),
    api<{ resolved: { set_code: string | null }; sets: { code: string; name: string }[] }>(
      `/divisions?project_id=${encodeURIComponent(id)}`,
    ).catch(() => null),
  ]);
  // A failed load must not read as "No estimate yet. Make the first version".
  if (versionsRes.failed) return <LoadFailed what="the estimates" />;
  const versions = versionsRes.data ?? [];
  let scopes: Awaited<ReturnType<typeof listScopes>> = [];
  let scopesError: string | null = null;
  try {
    scopes = await listScopes(id);
  } catch (e) {
    scopesError = (e as Error).message || "could not be loaded";
  }
  const latestScopeId =
    [...scopes].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))[0]?.id ?? null;

  const newestFirst = [...versions].sort((a, b) => b.version_number - a.version_number);
  const current = versions.find((v) => v.is_active) ?? null;
  const nextVersion = (newestFirst[0]?.version_number ?? 0) + 1;
  const acc = readiness?.accuracy;
  const setCode = divisions?.resolved.set_code ?? null;
  const setName = setCode ? divisions?.sets.find((s) => s.code === setCode)?.name ?? setCode : null;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <Box className={`p-5 ${current ? "border-2 border-slate-900" : ""}`}>
          <Label>Current estimate</Label>
          {current ? (
            <>
              <div className="mt-1 flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <span className="text-3xl font-bold tabular-nums">{fmtMoney(current.totals?.with_gst ?? null)}</span>
                <span className="text-sm text-slate-600">
                  v{current.version_number} · {fmtDate(current.created_at)}
                  {current.totals?.detail_level ? ` · ${current.totals.detail_level.replace(/_/g, " ")}` : ""}
                </span>
              </div>
              <p className="m-0 mt-1 text-sm text-slate-600">
                {fmtMoney(current.totals?.before_gst ?? null)} before GST. Invoices, the schedule of values and the
                contract read this version.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Link href={`/projects/${id}/estimate/${current.id}`} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white">
                  Open v{current.version_number}
                </Link>
                <a
                  href={`/api/workbench/client-package-pdf/${id}?doc=merged`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="rounded-md border border-slate-900 px-3 py-1.5 text-sm"
                >
                  Client package PDF
                </a>
              </div>
            </>
          ) : (
            <p className="m-0 mt-1 text-sm text-slate-600">
              {versions.length ? "No version is current. Keep one below." : "No estimate yet. Make the first version below."}
            </p>
          )}
        </Box>

        <Box className="p-5">
          <Label>How solid is it</Label>
          {acc ? (
            <div className="mt-1 space-y-1 text-[13px] text-slate-700">
              <div className="text-base font-semibold">{acc.coverage_pct}% measured from drawings</div>
              <div>{acc.headline}</div>
              {acc.measured_divisions.length > 0 && <div>Measured: {acc.measured_divisions.join(", ")}</div>}
              {acc.allowance_divisions.length > 0 && (
                <div>Priced as allowances (not measured from a CAD drawing): {acc.allowance_divisions.join(", ")}</div>
              )}
              {acc.allowance_divisions.length > 0 && (
                <Link href={`/projects/${id}/documents`} className="text-blue-700">
                  Upload the missing drawings →
                </Link>
              )}
            </div>
          ) : (
            <p className="m-0 mt-1 text-sm text-slate-500">Could not be worked out.</p>
          )}
        </Box>
      </div>

      <Box className="space-y-3 p-5">
        <Label>Before the next version</Label>
        <BeforeNextVersion projectId={id} divisionSet={setName} />
      </Box>

      <Box className="space-y-4 p-5">
        <Label>Make a new version</Label>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-md border border-slate-300 p-3">
            <div className="font-semibold">Full estimate</div>
            <p className="m-0 mb-2 text-[13px] text-slate-600">
              From the scope, the checked takeoff and the rate library. About 6 minutes; you can leave the page. It
              arrives as a draft and does not replace the current version.
            </p>
            {scopesError ? (
              <p className="m-0 text-sm text-red-700">
                The scope list could not be loaded, so an estimate cannot be started. This is not the same as having
                no scope. {scopesError}
              </p>
            ) : (
              <GenerateEstimateButtons projectId={id} scopeId={latestScopeId} disciplinePlan={readiness?.discipline_plan} />
            )}
          </div>
          <div className="rounded-md border border-slate-300 p-3">
            <div className="font-semibold">Change the current one</div>
            {current ? (
              <ChangeByDescribing
                projectId={id}
                versionId={current.id}
                label={`v${current.version_number}`}
                nextVersion={nextVersion}
              />
            ) : (
              <p className="m-0 text-[13px] text-slate-600">There is no current version to change.</p>
            )}
          </div>
        </div>
      </Box>

      <Box className="overflow-x-auto p-0">
        <div className="px-5 pt-4">
          <Label>All versions</Label>
        </div>
        <table className="mt-2 w-full min-w-[640px] text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
            <tr className="border-b border-slate-300">
              <th className="px-5 py-2">Version</th>
              <th className="px-3 py-2 text-right">Total with GST</th>
              <th className="px-3 py-2">Made</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {newestFirst.map((v) => {
              const draft = !v.is_active;
              const how = v.revision
                ? `changed from v${v.revision.from_version}: ${v.revision.instruction}`
                : v.ai_job_id || v.provenance
                  ? "written by AI"
                  : "by hand";
              return (
                <tr key={v.id} className={`border-b border-slate-100 ${v.is_active ? "bg-slate-50" : ""}`}>
                  <td className="px-5 py-2.5">
                    <Link href={`/projects/${id}/estimate/${v.id}`} className="font-semibold text-blue-700">
                      v{v.version_number}
                    </Link>
                    {/* The reference is the estimate's name (e.g. which scope of a
                        two-scope project it prices), so it belongs in the list. */}
                    {v.reference && <span className="ml-2 text-xs font-medium text-slate-700">{v.reference}</span>}
                    <div className="max-w-md truncate text-xs text-slate-500" title={how}>
                      {how}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{fmtMoney(v.totals?.with_gst ?? null)}</td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-slate-600">{fmtDate(v.created_at)}</td>
                  <td className="px-3 py-2.5">
                    {v.is_active ? (
                      <span className="rounded border border-slate-900 px-1.5 py-0.5 text-xs">Current</span>
                    ) : (
                      <span className="text-xs text-orange-800">Draft · not kept</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap items-start gap-2">
                      {current && v.id !== current.id && (
                        <Link
                          href={`/projects/${id}/workbench/estimates/${v.id}/diff/${current.id}`}
                          className="text-xs text-blue-700"
                        >
                          Compare with current
                        </Link>
                      )}
                      {draft && <SetActiveButton projectId={id} versionId={v.id} label="Keep (make current)" />}
                      {draft && <DiscardDraftButton versionId={v.id} label={`v${v.version_number}`} />}
                    </div>
                  </td>
                </tr>
              );
            })}
            {newestFirst.length === 0 && (
              <tr>
                <td colSpan={5} className="px-5 py-4 text-slate-500">
                  No versions yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Box>
    </div>
  );
}
