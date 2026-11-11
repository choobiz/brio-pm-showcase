"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

/**
 * Checks once that the person may open this project before any section loads.
 *
 * Rollout test 2026-10-15, as a manager not on 1234: Overview showed "Something
 * went wrong" and Money "no access to this project" under the full tab bar.
 * No data leaked (every call was refused), but nothing said plainly what had
 * happened. Now a refused or missing project gets one clear page instead.
 */
export function ProjectGate({ id, children }: { id: string; children: React.ReactNode }) {
  const [state, setState] = useState<"checking" | "ok" | "denied" | "missing">("checking");

  useEffect(() => {
    let live = true;
    fetch(`/api/backend/projects/${encodeURIComponent(id)}`, { cache: "no-store" })
      .then((r) => {
        if (!live) return;
        // Anything other than a refusal or a missing project (a restart, a 502)
        // lets the page load and report its own error, as before.
        setState(r.status === 403 ? "denied" : r.status === 404 || r.status === 422 ? "missing" : "ok");
      })
      .catch(() => live && setState("ok"));
    return () => {
      live = false;
    };
  }, [id]);

  if (state === "checking") return null;
  if (state === "ok") return <>{children}</>;
  return (
    <div className="mx-auto mt-10 max-w-md rounded-lg border border-slate-200 bg-white p-6 text-center">
      <h1 className="m-0 text-lg font-semibold text-slate-900">
        {state === "denied" ? "You don't have access to this project" : "Project not found"}
      </h1>
      <p className="mt-2 text-sm text-slate-600">
        {state === "denied"
          ? "It isn't one of the projects assigned to you. If you need it, ask Amir to add you to it."
          : "This link doesn't match a project. It may have been removed, or the link is incomplete."}
      </p>
      <Link href="/projects" className="mt-4 inline-block text-sm text-blue-700">
        Go to your projects
      </Link>
    </div>
  );
}
