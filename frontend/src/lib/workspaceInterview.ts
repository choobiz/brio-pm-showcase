// The questions outstanding on a project, each carrying what the answer buys.
//
// ⚠️ READ-ONLY. The interview never writes. A question that can be answered in
// place says how in `answer`: which project field to PATCH and what each option
// writes, so the Overview writes the project record itself (one way to write
// each field). Anything else, e.g. reviewing quantities, carries an `action`
// link to where it is actually done.

import { workspaceGet } from "@/lib/workspaceApi";

export interface InterviewQuestion {
  id: string;
  question: string;
  /** What the answer buys. The whole reason this is an interview and not a form. */
  why: string;
  blocking: boolean;
  kind: string;
  options: string[] | null;
  /** Where it is answered, when that is somewhere else. */
  action: string | null;
  current: unknown;
  /** How to answer in place, when it can be: PATCH /projects/{id} with
   *  `field` = values[i] for option i. A null value means that option is
   *  answered elsewhere (e.g. "Upload them"). For drawing decisions the field
   *  is a map and `key` names the entry. No `values` = free entry (a year). */
  answer: { field: string; key?: string; values?: (string | null)[] } | null;
}

export interface Interview {
  project_id: string;
  project_name: string;
  blocking: InterviewQuestion[];
  material: InterviewQuestion[];
  answered: string[];
  /** Declared up front — an open-ended interrogation is worse than a form. */
  summary: string;
  confidence: string | null;
  coverage_pct: number | null;
  can_estimate: boolean;
  stopping_rule: string;
  /** Why no question states a dollar amount. */
  no_figures_note: string;
}

export function getInterview(projectId: string): Promise<Interview> {
  return workspaceGet<Interview>(
    `/interview?project_id=${encodeURIComponent(projectId)}`,
  );
}
