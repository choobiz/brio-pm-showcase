"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { listProjects, type ProjectRead } from "@/lib/projects";

export default function FieldHome() {
  const [projects, setProjects] = useState<ProjectRead[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    listProjects()
      .then((ps) => setProjects(ps.filter((p) => p.status !== "archived")))
      .catch((e) => setErr(String(e)));
  }, []);

  return (
    <div className="space-y-3">
      <h1 className="px-1 text-lg font-semibold text-slate-800">Select a project</h1>
      {err && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          Couldn&rsquo;t load projects.{" "}
          <a className="font-medium underline" href="/sign-in">Sign in</a>
        </div>
      )}
      {(projects ?? []).map((p) => (
        <Link
          key={p.id}
          href={`/field/${p.id}`}
          className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm active:bg-slate-50"
        >
          <div className="font-medium text-slate-900">{p.name}</div>
          {p.address && <div className="text-xs text-slate-500">{p.address}</div>}
          <div className="mt-1 text-[10px] uppercase tracking-wide text-slate-400">
            {p.status.replace("_", " ")}
          </div>
        </Link>
      ))}
      {projects && projects.length === 0 && (
        <div className="px-1 text-sm text-slate-400">No projects.</div>
      )}
      {!projects && !err && <div className="px-1 text-sm text-slate-400">Loading…</div>}
    </div>
  );
}
