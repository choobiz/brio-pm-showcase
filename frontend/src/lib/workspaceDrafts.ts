// What has been drafted and not issued, and taking one back.

import { workspaceGet } from "@/lib/workspaceApi";

export type DraftKind = "estimate_version" | "change_order" | "invoice";

export interface DraftItem {
  kind: DraftKind;
  label: string;
  id: string;
  title: string;
  /**
   * ⚠️ THREE STATES, NOT TWO. "ai" means a run is recorded against it;
   * "not recorded" means no link was captured — which is NOT a claim that a
   * person wrote it. Every record predating 2026-10-02 reads this way because
   * the column did not exist, and rendering that as human-authored would invent
   * a fact about who produced a commercial document.
   */
  drafted_by: "ai" | "not recorded";
  ai_job_id: string | null;
  created_at: string | null;
}

export interface DraftsResponse {
  items: DraftItem[];
  total: number;
  traced: number;
  untraced_note: string;
}

export function listDrafts(projectId: string): Promise<DraftsResponse> {
  return workspaceGet<DraftsResponse>(
    `/drafts?project_id=${encodeURIComponent(projectId)}`,
  );
}

/** DELETE with a body, which `workspacePost` cannot express — but the error
 *  handling must still match, so the response is funnelled through the same
 *  shape workspaceApi produces rather than throwing a raw body. */
export async function discardDraft(
  kind: DraftKind, id: string, reason?: string,
): Promise<{ deleted: boolean; revision_number: number | null }> {
  const res = await fetch(
    `/api/backend/drafts/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason ?? null }),
      cache: "no-store",
    },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let detail: string | null = null;
    try {
      const d = JSON.parse(text)?.detail;
      if (typeof d === "string" && d.length < 400 && !d.startsWith("<")) detail = d;
    } catch {
      /* not JSON — the status decides */
    }
    throw new Error(
      detail ??
        (res.status >= 500
          ? "The server couldn't discard that. Try again."
          : "That couldn't be discarded."),
    );
  }
  return res.json();
}
