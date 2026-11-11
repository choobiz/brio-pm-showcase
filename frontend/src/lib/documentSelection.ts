import { apiError } from "./apiError";
// Which of a project's documents feed the scope, and which get measured.
//
// Replaces the `category === "drawing"` filter that used to make this decision
// invisibly in the backend. See backend/app/services/document_selection.py.

const BASE = "/api/backend";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) throw await apiError(res);
  return res.json() as Promise<T>;
}

export type DocFormat = "cad" | "pdf" | "image" | "text" | "other";

export interface SelectableDocument {
  document_id: string;
  filename: string;
  category: string;
  mime: string | null;
  size_bytes: number | null;
  created_at: string | null;
  /** null = nobody has ruled on this document yet. */
  use_for_scope: boolean | null;
  use_for_quantities: boolean | null;
  /** Set when an identical filename + size appeared earlier in the list. */
  duplicate_of: string | null;
  format: DocFormat;
  /** Inferred from the filename; null when the format carries no discipline. */
  discipline: string | null;
  can_scope: boolean;
  can_quantities: boolean;
  scope_blocked_reason: string | null;
  recommend_scope: boolean;
  recommend_quantities: boolean;
  scope_reason: string | null;
  quantities_reason: string | null;
}

/** Shape of estimate_accuracy.Accuracy.as_dict(). */
export interface AccuracyGrade {
  grade: string;
  headline: string;
  coverage_pct: number;
  disciplines: Record<string, unknown>;
  measured_divisions: string[];
  allowance_divisions: string[];
  next_uploads: { label?: string; why?: string }[];
  warnings: string[];
}

export interface SelectionResponse {
  documents: SelectableDocument[];
  accuracy: AccuracyGrade;
  disciplines: Record<string, { cad: boolean; pdf: boolean }>;
  counts: Record<string, number>;
  selected_scope: number;
  selected_quantities: number;
  total_documents: number;
  has_selection: boolean;
  saved?: number;
}

export const getDocumentSelection = (projectId: string) =>
  api<SelectionResponse>(`/projects/${projectId}/document-selection`);

export const putDocumentSelection = (
  projectId: string,
  entries: {
    document_id: string;
    use_for_scope: boolean;
    use_for_quantities: boolean;
  }[],
) =>
  api<SelectionResponse>(`/projects/${projectId}/document-selection`, {
    method: "PUT",
    body: JSON.stringify({ entries }),
  });
