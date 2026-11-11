// The drawing register — which files are the same sheet, and which revision is
// current. A PROPOSAL worked out from filenames; nothing is stored.

import { workspaceGet, workspacePost } from "@/lib/workspaceApi";


export interface RegisterRevision {
  id: string;
  filename: string;
  size_bytes: number | null;
  /** "R3", "Rev B", "PRELIM" — as written on the filename, or null. */
  revision: string | null;
  /** ISO date read off the filename, or null. */
  issued: string | null;
  is_current: boolean;
}

export interface RegisterSheet {
  key: string;
  /** Reported, never used to group — a sheet number is only unique within one
   *  originator, and three buildings on one site share a numbering scheme. */
  sheet_number: string | null;
  label: string;
  revisions: RegisterRevision[];
  revision_count: number;
  has_history: boolean;
  grouped_because: "date_token" | "revision_token" | "name_only" | "confirmed";
  grouped_because_plain: string;
  /** "confirmed" = a person accepted this grouping. "proposed" = worked out
   *  from filenames and not saved. */
  status: "confirmed" | "proposed";
  reviewed_at: string | null;
}

export interface RegisterDuplicate {
  id: string;
  filename: string;
  duplicate_of: string;
  duplicate_of_filename: string;
  reason: string;
}

export interface DrawingRegister {
  sheets: RegisterSheet[];
  duplicates: RegisterDuplicate[];
  summary: {
    documents_in: number;
    duplicates_found: number;
    distinct_drawings: number;
    drawings_with_more_than_one_revision: number;
    superseded_files: number;
    confirmed_drawings: number;
    proposed_drawings: number;
  };
  /** A hash of the grouping being shown. Sent back when confirming, so a
   *  drawing uploaded in the meantime refuses the confirmation instead of
   *  being swept into a grouping nobody looked at. */
  fingerprint: string;
  caveat: string;
}

export async function getDrawingRegister(projectId: string): Promise<DrawingRegister> {
  return workspaceGet<DrawingRegister>(
    `/documents/drawing-register?project_id=${encodeURIComponent(projectId)}`,
  );
}


/** Record the proposed grouping as confirmed.
 *
 *  Sends back the fingerprint of what was on screen. A 409 means the drawings
 *  changed since then and NOTHING was written — reload and look again. */
export async function confirmDrawingRegister(
  projectId: string,
  fingerprint: string,
): Promise<{ drawings_confirmed: number; files_recorded: number }> {
  const p = new URLSearchParams({ project_id: projectId, fingerprint });
  // The 409 path matters most here: workspaceApi lifts our own `detail.message`
  // out of the body, so the stale-fingerprint explanation still reaches the
  // screen verbatim — what it no longer does is show a proxy's HTML when the
  // failure happened before the request ever reached the API.
  return workspacePost<{ drawings_confirmed: number; files_recorded: number }>(
    `/documents/drawing-register/confirm?${p}`,
  );
}
