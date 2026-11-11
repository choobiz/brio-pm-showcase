"use client";

import { useState } from "react";

import { sharePresentationAction } from "./actions";

/** Generates a public, tokenized client link to the proposal PDF and copies it. */
export function ShareLinkButton({ projectId, presentationId }: { projectId: string; presentationId: string }) {
  const [state, setState] = useState<"idle" | "working" | "copied" | "error">("idle");
  const [url, setUrl] = useState<string | null>(null);

  const run = async () => {
    setState("working");
    try {
      const res = await sharePresentationAction(projectId, presentationId);
      setUrl(res.url);
      try {
        await navigator.clipboard.writeText(res.url);
        setState("copied");
      } catch {
        setState("idle"); // clipboard blocked — show the link to copy manually
      }
    } catch {
      setState("error");
    }
  };

  return (
    <span className="inline-flex items-center gap-2">
      <button
        onClick={run}
        disabled={state === "working"}
        className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:border-blue-500 hover:text-blue-700 disabled:opacity-60"
      >
        {state === "working" ? "…" : state === "copied" ? "Link copied ✓" : "🔗 Client link"}
      </button>
      {url && state !== "copied" && (
        <input readOnly value={url} onFocus={(e) => e.currentTarget.select()} className="w-56 rounded border border-slate-300 px-2 py-1 text-xs text-slate-600" />
      )}
      {state === "error" && <span className="text-xs text-red-600">Failed</span>}
    </span>
  );
}
