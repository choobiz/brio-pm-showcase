"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { createAIJob, streamAIJob, type AIJobStreamEvent } from "@/lib/aiJobs";

/**
 * Fires a `presentation` job that assembles the active estimate + scope +
 * schedule + cash flow + drawings into a client deck (draft). Numbers are
 * pulled verbatim; one Opus call writes the framing/work-area notes/approval
 * items. Owner-only on the backend.
 */
export function GeneratePresentationButton({
  projectId,
  hasEstimate,
}: {
  projectId: string;
  hasEstimate: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = () => {
    if (!hasEstimate) {
      setError("This project has no estimate yet — generate an estimate first.");
      return;
    }
    setBusy(true);
    setError(null);
    setProgress("Queued…");
    createAIJob({ project_id: projectId, kind: "presentation", input: { audience: "client" } })
      .then((job) => {
        const es = streamAIJob(job.id);
        es.addEventListener("status", (ev) => {
          try {
            const p = JSON.parse((ev as MessageEvent).data) as AIJobStreamEvent;
            setProgress(p.status === "running" ? "Assembling the deck…" : `${p.status}…`);
            if (p.status === "done") {
              es.close();
              setBusy(false);
              setProgress(null);
              router.refresh();
            } else if (p.status === "error") {
              es.close();
              setBusy(false);
              setProgress(null);
              setError(p.error ?? "Generation failed");
            }
          } catch {
            /* ignore malformed SSE frames */
          }
        });
        es.onerror = () => {
          es.close();
          setBusy(false);
          setProgress(null);
          setError("Lost connection to the generator.");
        };
      })
      .catch((e) => {
        setBusy(false);
        setProgress(null);
        setError(e instanceof Error ? e.message : String(e));
      });
  };

  return (
    <div className="space-y-2">
      <button
        onClick={run}
        disabled={busy}
        className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        style={{ backgroundColor: "#0068A0" }}
      >
        {busy ? progress ?? "Generating…" : "Generate client presentation"}
      </button>
      <p className="text-xs text-slate-500">
        Pulls the active estimate, scope, schedule &amp; cash flow into a client deck (~1 min). Creates a draft you review before sharing.
      </p>
      {error && (
        <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
      )}
    </div>
  );
}
