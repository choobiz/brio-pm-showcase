// Division sets — the cost breakdown an estimate is organised into.
// Read-only: changing a set changes every future estimate on every project
// that uses it, so editing lives with the other company-wide changes.

import { workspaceGet } from "@/lib/workspaceApi";


export interface Division {
  number: string;
  title: string;
  contingency_pct: number;
  /** CSI records all 50 numbers; only the ones Brio plausibly prices are on. */
  used_by_default: boolean;
  /** A reserved number has no meaning and is never offered. */
  reserved: boolean;
}

export interface DivisionSet {
  id: string;
  code: string;
  name: string;
  standard: string;
  version: string | null;
  source_ref: string | null;
  notes: string | null;
  project_types: string[];
  divisions: Division[];
  counts: { total: number; usable: number; used_by_default: number; reserved: number };
}

export interface DivisionsResponse {
  sets: DivisionSet[];
  resolved: {
    project_name: string | null;
    project_type: string | null;
    set_code: string | null;
    why: string;
  } | null;
  editable: boolean;
  editable_note: string;
}

export async function getDivisions(projectId?: string): Promise<DivisionsResponse> {
  const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  return workspaceGet<DivisionsResponse>(`/divisions${q}`);
}
