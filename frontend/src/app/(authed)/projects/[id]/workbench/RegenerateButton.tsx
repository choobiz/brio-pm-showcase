"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { regenerateDownstreamAction } from "./actions";

/**
 * Phase 3a Step 5 — small client button inside StaleBanner.
 * Fires the server action that rebases the downstream artifacts on the
 * project's active estimate, then refreshes so the banner re-reads.
 */
export function RegenerateButton({ projectId }: { projectId: string }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        disabled={pending}
        onClick={() => {
          setError(null);
          startTransition(async () => {
            try {
              await regenerateDownstreamAction(projectId);
              router.refresh();
            } catch (e) {
              setError(e instanceof Error ? e.message : "Regenerate failed");
            }
          });
        }}
        className="ml-2 px-2 py-0.5 text-[10px] uppercase tracking-wide font-semibold rounded border border-amber-300 bg-white hover:bg-amber-100 disabled:opacity-50"
      >
        {pending ? "Regenerating…" : "Regenerate"}
      </button>
      {error && <span className="ml-2 text-[10px] text-red-700">{error}</span>}
    </>
  );
}
