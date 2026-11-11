"use client";

import { useEffect, useState } from "react";

import { usePortal } from "../portalCtx";
import { getDocuments, packageUrl, type PortalDoc } from "@/lib/portal";

const CATEGORY_LABEL: Record<string, string> = {
  contract: "Contract",
  permit: "Permit",
  report: "Report",
  drawing: "Drawing",
  other: "Document",
};

export default function Documents() {
  const { project, loading } = usePortal();
  const [docs, setDocs] = useState<PortalDoc[] | null>(null);

  useEffect(() => {
    if (project) getDocuments(project.id).then(setDocs).catch(() => setDocs([]));
  }, [project]);

  if (loading || !project) return <div className="h-40" />;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold" style={{ color: "#1D3146" }}>Documents</h1>

      {/* Client package — always available, generated on demand */}
      <a href={packageUrl(project.id)} target="_blank" rel="noopener noreferrer"
        className="flex items-center justify-between rounded-2xl border bg-white p-4 shadow-sm transition hover:shadow-md"
        style={{ borderColor: "#ece8e2" }}>
        <div>
          <div className="font-medium text-slate-800">Your project package</div>
          <div className="text-xs text-slate-400">Overview, schedule, estimate &amp; contract — one PDF</div>
        </div>
        <span className="text-sm font-medium" style={{ color: "#0068A0" }}>Open ↗</span>
      </a>

      <div className="space-y-2">
        {docs?.map((d, i) => (
          <a key={i} href={d.url} target="_blank" rel="noopener noreferrer"
            className="flex items-center justify-between rounded-xl border bg-white px-4 py-3 shadow-sm transition hover:shadow-md"
            style={{ borderColor: "#ece8e2" }}>
            <div>
              <div className="text-sm font-medium text-slate-800">{d.name}</div>
              <div className="text-[11px] uppercase tracking-wide text-slate-400">
                {CATEGORY_LABEL[d.category] ?? d.category}
              </div>
            </div>
            <span className="text-sm" style={{ color: "#0068A0" }}>↓</span>
          </a>
        ))}
        {docs && docs.length === 0 && (
          <p className="text-sm text-slate-400">Shared documents will appear here.</p>
        )}
      </div>
    </div>
  );
}
