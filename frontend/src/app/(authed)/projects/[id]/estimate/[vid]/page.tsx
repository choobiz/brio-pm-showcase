import Link from "next/link";
import { loadOr } from "@/lib/loadOr";
import { LoadFailed } from "@/components/LoadFailed";
import { Fragment } from "react";

import { Box, Label } from "@/components/kit";
import { api } from "@/lib/api";
import { fmtDate, fmtMoney } from "@/lib/fmt";
import { scopePackEntry, type PdfScope, type ScopeForMenu } from "@/lib/scopePackLinks";
import { getEstimateReadiness, getEstimateVersion, listDrawingLinks, listEstimateVersions } from "@/lib/workbench";

import { CompareDropdown } from "../../workbench/estimates/[vid]/CompareDropdown";
import { PackageStatusBanner } from "../../workbench/estimates/[vid]/PackageStatusBanner";
import { SetActiveButton } from "../../workbench/estimates/SetActiveButton";
import { ChangeByDescribing } from "../EstimateIslands";
import { DivisionTable, type Category } from "./DivisionTable";
import { DivisionPdfMenuItem } from "./DivisionPdfMenuItem";
import { EmailEstimateButton } from "./EmailEstimateButton";
import { ScopePackMenuItem } from "./ScopePackMenuItem";

/**
 * One estimate version — round-1 wireframe. Total and actions up top, change
 * it by describing it (saved as a NEW draft version), the divisions with
 * where each came from, and "why this number" for a clicked line.
 */
export default async function EstimateVersionPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string; vid: string }>;
  searchParams: Promise<{ view?: string }>;
}) {
  const { id, vid } = await params;
  const clientView = (await searchParams).view === "client";
  const cb = Date.now();
  const [v, allRes, linksRes, readiness, scopesRes, pdfScope] = await Promise.all([
    getEstimateVersion(id, vid),
    loadOr("estimate versions", listEstimateVersions(id)),
    loadOr("drawing links", listDrawingLinks(id, vid)),
    getEstimateReadiness(id).catch(() => null),
    loadOr("scopes", api<ScopeForMenu[]>(`/scopes?project_id=${encodeURIComponent(id)}`)),
    api<PdfScope>(`/projects/${id}/workbench/estimate-versions/${vid}/pdf-scope`).catch(() => null),
  ]);
  const all = allRes.data ?? [];
  const links = linksRes.data ?? [];
  const scopes = scopesRes.data ?? [];
  const payload = (v.payload ?? {}) as {
    categories?: Category[];
    assumptions?: string[];
    exclusions?: string[];
    _revision?: { from_version: number | null; instruction: string };
  };
  const cats = payload.categories ?? [];
  const lines = cats.reduce((n, c) => n + (c.items ?? []).filter((i) => !i.contingency).length, 0);
  const t = v.totals ?? null;
  const nextVersion = Math.max(0, ...all.map((x) => x.version_number)) + 1;
  const compareOptions = all
    .filter((x) => x.id !== vid)
    .sort((a, b) => b.version_number - a.version_number)
    .map((x) => ({ id: x.id, version_number: x.version_number, reference: x.reference }));
  const pdfs: [string, string][] = [
    ["Internal, with costs", `/api/workbench/estimate-pdf/${id}/${vid}`],
    ["Client: summary", `/api/workbench/estimate-client-pdf/${id}/${vid}?summary=true`],
    ["Client: detailed", `/api/workbench/estimate-client-pdf/${id}/${vid}`],
    ["Client: residential", `/api/workbench/estimate-client-pdf/${id}/${vid}?residential=true`],
    ["Client: material selection", `/api/workbench/estimate-selection-pdf/${id}/${vid}?mode=selection&_cb=${cb}`],
    ["Client: lump sum", `/api/workbench/estimate-selection-pdf/${id}/${vid}?mode=lumpsum&_cb=${cb}`],
    ["Internal: fee breakdown", `/api/workbench/estimate-client-pdf/${id}/${vid}?internal_fee=true`],
    ["Client package (current version)", `/api/workbench/client-package-pdf/${id}?doc=merged`],
  ];
  const viewHref = (client: boolean) => `/projects/${id}/estimate/${vid}${client ? "?view=client" : ""}`;

  return (
    <div className="space-y-4">
      <div className="text-[13px]">
        <Link href={`/projects/${id}/estimate`} className="text-blue-700">
          Estimate
        </Link>{" "}
        <span className="text-slate-500">/ v{v.version_number}</span>
      </div>

      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <Label>
            Estimate v{v.version_number} · {v.is_active ? "current" : "draft, not kept"}
          </Label>
          <div className="mt-1 text-3xl font-bold tabular-nums">{fmtMoney(t?.with_gst ?? null)}</div>
          <div className="text-[13px] text-slate-600">
            {fmtDate(v.created_at)}
            {t?.detail_level ? ` · ${t.detail_level.replace(/_/g, " ")}` : ""} · {lines} lines
            {payload._revision ? ` · changed from v${payload._revision.from_version}: ${payload._revision.instruction}` : ""}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex overflow-hidden rounded-md border border-slate-900 text-[13px]" role="group" aria-label="View">
            <Link href={viewHref(false)} className={`px-3 py-1.5 ${!clientView ? "bg-slate-900 text-white" : ""}`}>
              Our view
            </Link>
            <Link href={viewHref(true)} className={`px-3 py-1.5 ${clientView ? "bg-slate-900 text-white" : ""}`}>
              Client&apos;s view
            </Link>
          </div>
          {compareOptions.length > 0 && <CompareDropdown projectId={id} vid={vid} options={compareOptions} />}
          <Link
            href={`/projects/${id}/workbench/estimates/${vid}/edit`}
            className="rounded-md border border-slate-900 px-3 py-1.5 text-sm"
          >
            Edit lines
          </Link>
          <EmailEstimateButton projectId={id} vid={vid} />
          <details className="relative">
            <summary className="cursor-pointer list-none rounded-md border border-slate-900 px-3 py-1.5 text-sm">PDF ▾</summary>
            <div className="absolute right-0 z-10 mt-1 w-80 rounded-md border border-slate-300 bg-white p-1 shadow">
              {pdfs.map(([label, href]) => (
                <Fragment key={label}>
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block rounded px-2 py-1.5 text-sm hover:bg-slate-100"
                  >
                    {label}
                  </a>
                  {label === "Client: detailed" && (
                    <>
                      <DivisionPdfMenuItem projectId={id} vid={vid} />
                      <DivisionPdfMenuItem projectId={id} vid={vid} withLines />
                    </>
                  )}
                  {label === "Client: lump sum" && (
                    <ScopePackMenuItem
                      entry={scopePackEntry(pdfScope, `/api/workbench/estimate-selection-pdf/${id}/${vid}`, cb)}
                      scopes={scopes}
                    />
                  )}
                </Fragment>
              ))}
            </div>
          </details>
          {!v.is_active && <SetActiveButton projectId={id} versionId={vid} label="Keep (make current)" />}
        </div>
      </div>

      <PackageStatusBanner projectId={id} />

      <Box className="p-4">
        <div className="mb-1 text-[15px] font-semibold">Change it by describing it</div>
        <ChangeByDescribing projectId={id} versionId={vid} label={`v${v.version_number}`} nextVersion={nextVersion} />
      </Box>

      {clientView && (
        <p className="m-0 text-[13px] text-slate-600">
          Client&apos;s view: overhead and profit are spread across the divisions, and labour, materials and line prices
          are hidden, as on the client documents.
        </p>
      )}

      {(allRes.failed || linksRes.failed || scopesRes.failed) && (
        <LoadFailed what={allRes.failed ? "the other versions (compare and next version number may be wrong)"
          : linksRes.failed ? "the drawing links" : "the scopes for the PDF menu"} small />
      )}
      <DivisionTable
        projectId={id}
        categories={cats}
        totals={t}
        measured={readiness?.accuracy.measured_divisions ?? []}
        allowance={readiness?.accuracy.allowance_divisions ?? []}
        links={links}
        clientView={clientView}
        assumptions={(payload.assumptions ?? []).filter((x) => typeof x === "string" && x.trim())}
        exclusions={(payload.exclusions ?? []).filter((x) => typeof x === "string" && x.trim())}
      />
    </div>
  );
}
