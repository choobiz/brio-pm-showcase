"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { getGettingStarted, type GettingStarted } from "@/lib/gettingStarted";

const CATEGORY_LABEL: Record<string, string> = {
  drawing: "Drawing",
  report: "Report",
  scope: "Scope / SoW",
  permit: "Permit",
  photo: "Photo",
  client_note: "Client Note",
  contract: "Contract",
  other: "Other",
};

const BUCKET_LABEL: Record<string, string> = {
  new_construction: "New construction",
  renovation: "Renovation",
  general: "General",
};

/**
 * Setup guide shown on new/early projects: the right order of first steps
 * (documents → scope → estimate) plus the type-tailored "minimum documents
 * for a reliable estimate" checklist. Auto-hides once an estimate exists;
 * dismissable in the meantime.
 */
export function GettingStartedGuide({ projectId }: { projectId: string }) {
  const [gs, setGs] = useState<GettingStarted | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    getGettingStarted(projectId)
      .then(setGs)
      .catch(() => setGs(null));
  }, [projectId]);

  // Hide once fully set up, or if the operator dismissed it this session.
  if (!gs || gs.next_step === "done" || dismissed) return null;

  const required = gs.documents.filter((d) => d.required);
  const recommended = gs.documents.filter((d) => !d.required);

  return (
    <Card className="p-4 border-blue-200 bg-blue-50/40">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-blue-800">
            Getting started
          </h2>
          <p className="mt-0.5 text-sm text-slate-600">
            {gs.project_type ? `${BUCKET_LABEL[gs.bucket]} project. ` : ""}
            Add documents first so scope analysis and a reliable estimate can run.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          className="text-xs text-slate-400 hover:text-slate-600"
          title="Dismiss for now"
        >
          Dismiss
        </button>
      </div>

      {/* Step tracker */}
      <ol className="mt-3 flex flex-wrap gap-2 text-xs">
        {gs.steps.map((s, i) => {
          const active =
            !s.done && gs.steps.slice(0, i).every((p) => p.done);
          return (
            <li
              key={s.key}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${
                s.done
                  ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                  : active
                    ? "border-blue-400 bg-white text-blue-800 font-medium"
                    : "border-slate-200 bg-white text-slate-400"
              }`}
            >
              <span>{s.done ? "✓" : i + 1}</span>
              {s.label}
            </li>
          );
        })}
      </ol>

      {/* Step 1 detail — the type-tailored document checklist */}
      {!gs.state.has_documents && (
        <div className="mt-4">
          <div className="flex items-center justify-between">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Minimum documents for a reliable estimate
            </div>
            <Link
              href={`/projects/${projectId}/documents`}
              className="rounded bg-blue-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-blue-700"
            >
              + Add documents
            </Link>
          </div>
          <ul className="mt-2 space-y-1.5">
            {required.map((d) => (
              <li key={d.label} className="flex items-start gap-2 text-sm">
                <span className="mt-0.5 rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-rose-700">
                  Required
                </span>
                <span className="text-slate-700">
                  {d.label}{" "}
                  <span className="text-slate-400">
                    — {d.why} · upload as <b>{CATEGORY_LABEL[d.category] ?? d.category}</b>
                  </span>
                </span>
              </li>
            ))}
            {recommended.map((d) => (
              <li key={d.label} className="flex items-start gap-2 text-sm">
                <span className="mt-0.5 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold uppercase text-slate-500">
                  Suggested
                </span>
                <span className="text-slate-600">
                  {d.label}{" "}
                  <span className="text-slate-400">
                    — {d.why} · upload as <b>{CATEGORY_LABEL[d.category] ?? d.category}</b>
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Once docs exist, point to the next action */}
      {gs.state.has_documents && gs.next_step === "generate_scope" && (
        <div className="mt-3 text-sm">
          Documents added.{" "}
          <Link href={`/projects/${projectId}/scope`} className="font-medium text-blue-700 hover:underline">
            Generate the scope →
          </Link>
        </div>
      )}
      {gs.next_step === "generate_estimate" && (
        <div className="mt-3 text-sm">
          Scope ready.{" "}
          <Link href={`/projects/${projectId}/estimate`} className="font-medium text-blue-700 hover:underline">
            Generate the estimate →
          </Link>
        </div>
      )}
    </Card>
  );
}
