// Document search. There was none before — `GET /documents` filtered on
// project_id and nothing else, so finding a named drawing on the 4,019-file
// project meant scrolling.

import { workspaceGet } from "@/lib/workspaceApi";


export interface SearchHit {
  id: string;
  project_id: string;
  project_name: string;
  filename: string;
  category: string;
  mime: string | null;
  size_bytes: number | null;
  created_at: string | null;
  /** Which tier matched — stated, because a reader can act on "name matches
   *  exactly" and cannot act on a similarity score. */
  matched_on: string;
  /** Present only for a summary match: the text that matched. */
  snippet: string | null;
}

export interface SearchResponse {
  query: string;
  scope: "project" | "all-accessible";
  projects_searched: number;
  count: number;
  results: SearchHit[];
  /** Says plainly that "latest" is upload order, not revision order. Render it
   *  — documents have no revision chain yet and the UI must not imply one. */
  ordering_note: string;
}

export async function searchDocuments(
  q: string,
  opts?: { projectId?: string; category?: string; limit?: number },
): Promise<SearchResponse> {
  const p = new URLSearchParams({ q });
  if (opts?.projectId) p.set("project_id", opts.projectId);
  if (opts?.category) p.set("category", opts.category);
  if (opts?.limit) p.set("limit", String(opts.limit));
  return workspaceGet<SearchResponse>(`/documents/search?${p}`);
}
