"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { getProject } from "@/lib/projects";
import { getActivePunch, type TimeEntry } from "@/lib/timeTracking";

export default function FieldProjectMenu() {
  const { id } = useParams<{ id: string }>();
  const [name, setName] = useState("");
  const [active, setActive] = useState<TimeEntry | null>(null);

  useEffect(() => {
    getProject(id).then((p) => setName(p.name)).catch(() => {});
    getActivePunch().then((r) => setActive(r.active)).catch(() => {});
  }, [id]);

  const clockedInHere = active?.status === "open" && active.project_id === id;

  return (
    <div className="space-y-3">
      <Link href="/field" className="text-xs text-slate-500">← Projects</Link>
      <h1 className="px-1 text-lg font-semibold text-slate-900">{name || "Project"}</h1>

      {active?.status === "open" && (
        <div className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          You&rsquo;re on the clock{clockedInHere ? " here" : ` at ${active.project_name ?? "another project"}`}.
        </div>
      )}

      <Link
        href={`/field/${id}/report`}
        className="block rounded-xl border border-slate-200 bg-white p-5 shadow-sm active:bg-slate-50"
      >
        <div className="text-base font-semibold text-slate-900">📋 Field Review</div>
        <div className="mt-0.5 text-sm text-slate-500">Fill out today&rsquo;s daily site report.</div>
      </Link>

      <Link
        href={`/field/${id}/hours`}
        className="block rounded-xl border border-slate-200 bg-white p-5 shadow-sm active:bg-slate-50"
      >
        <div className="text-base font-semibold text-slate-900">⏱️ Log Hours</div>
        <div className="mt-0.5 text-sm text-slate-500">
          {clockedInHere ? "On the clock — punch out here." : "Punch in and out for this project."}
        </div>
      </Link>
    </div>
  );
}
