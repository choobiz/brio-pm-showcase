"use client";

import { use, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  getProjectIndex,
  materializeProjectIndex,
  reindexProject,
  type ProjectIndexRead,
} from "@/lib/documents";
import { useVisiblePolling } from "@/lib/usePolling";

/**
 * Project Index — the AI-generated per-project knowledge MD.
 *
 * Always renders from the source rows on load (cheap, always-fresh).
 * "Rebuild & save" force-writes the MD to R2 as a `project_index`
 * Document row so the file is downloadable / shareable / cacheable
 * elsewhere.
 */
export default function ProjectIndexPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: projectId } = use(params);
  const [data, setData] = useState<ProjectIndexRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<{
    download_url: string;
  } | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const next = await getProjectIndex(projectId);
      setData(next);
    } catch (e) {
      setError(String(e));
    }
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Re-poll every 10s while there's pending/running indexing work AND the tab
  // is in front. Work that never finishes would otherwise hold the gate open
  // forever, and a background tab learns nothing for the same cost as a
  // watched one.
  useVisiblePolling(refresh, 10000, !!data && data.pending_count > 0);

  const handleRebuild = async () => {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const res = await materializeProjectIndex(projectId);
      setSaved({ download_url: res.download_url });
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleReindexAll = async () => {
    setBusy(true);
    setError(null);
    try {
      await reindexProject(projectId, true);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        {error}
      </div>
    );
  }
  if (!data) {
    return <div className="text-sm text-slate-400">Loading index…</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between flex-wrap gap-3">
        <div>
          <h1
            className="text-2xl font-semibold"
            style={{ color: "var(--color-brio-accent)" }}
          >
            Project Index
          </h1>
          <p className="text-xs text-slate-500 mt-1">
            {data.document_count} documents · {data.summarized_count} indexed
            {data.pending_count > 0 && (
              <> · {data.pending_count} pending</>
            )}
            {data.failed_count > 0 && (
              <> · {data.failed_count} failed</>
            )}
            {data.last_indexed_at && (
              <>
                {" "}
                · last indexed{" "}
                {new Date(data.last_indexed_at).toLocaleString("en-CA", {
                  dateStyle: "medium",
                  timeStyle: "short",
                })}
              </>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" disabled={busy} onClick={handleReindexAll}>
            {busy ? "Working…" : "Reindex all"}
          </Button>
          <Button onClick={handleRebuild} disabled={busy}>
            {busy ? "Saving…" : "Rebuild & save .md"}
          </Button>
        </div>
      </div>

      {saved && (
        <Card className="p-3 bg-emerald-50 border-emerald-200">
          <div className="text-sm text-emerald-800 flex items-center justify-between gap-3 flex-wrap">
            <span>Saved <code>PROJECT_INDEX.md</code> to project storage.</span>
            <a
              href={saved.download_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-emerald-900 font-semibold underline"
            >
              Download
            </a>
          </div>
        </Card>
      )}

      <Card className="p-5 overflow-x-auto">
        <pre className="text-xs leading-relaxed text-slate-800 whitespace-pre-wrap break-words font-mono">
          {data.markdown}
        </pre>
      </Card>

      <p className="text-[10px] text-slate-400">
        This index is regenerated from <code>document_summaries</code> on
        every load. The .md file is only written to project storage when
        you click <em>Rebuild &amp; save</em>.
      </p>
    </div>
  );
}
