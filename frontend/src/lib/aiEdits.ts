// Edit a scope, estimate or change order by writing an instruction.
//
// The job PROPOSES a change set; this client reads it back, re-checked against
// the live record, and applies the changes a person accepted.

import { createAIJob } from "@/lib/aiJobs";
import { apiError } from "./apiError";

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

export type EditRecordType =
  | "scope"
  | "estimate"
  | "estimate_version"
  | "change_order";

export interface EditChange {
  path: string;
  action: "replace" | "remove" | "add";
  before?: unknown;
  after?: unknown;
  why?: string;
  /** What is actually at that path right now. Present when the path resolved. */
  actual?: unknown;
  applicable: boolean;
  /** Why it cannot be applied — a stale value, an invented path, a bad action. */
  problem: string | null;
}

export interface EditPlan {
  record_type: EditRecordType;
  record_id: string;
  instruction: string | null;
  summary: string | null;
  changes: EditChange[];
  applicable_count: number;
  blocked_count: number;
  /** What the model declined to do, plus what we declined on its behalf. */
  refused: string[];
  record_status: string | null;
  /** A reason to look twice, short of a refusal — e.g. editing the ACTIVE
   *  estimate version that invoices already bill from. */
  warning?: string | null;
  /** Sent, accepted or decided records cannot be edited in place. */
  locked: boolean;
  job_status: string;
}

export const getEditPlan = (jobId: string) => api<EditPlan>(`/ai-edits/${jobId}`);

export const applyEditPlan = (
  jobId: string,
  acceptPaths: string[],
  asNewVersion = false,
  /** Estimate only: the save step's rate-library decisions. */
  rateUpdates?: { key: string; write: boolean; reason?: string }[],
) =>
  api<{
    applied: number;
    skipped: number;
    applied_paths: string[];
    recomputed: Record<string, number>;
    record_id: string;
    /** True when the result was saved as the next version (scopes only). */
    new_version?: boolean;
  }>(`/ai-edits/${jobId}/apply`, {
    method: "POST",
    body: JSON.stringify({ accept_paths: acceptPaths, as_new_version: asNewVersion, rate_updates: rateUpdates }),
  });

export const requestEdit = (
  projectId: string,
  recordType: EditRecordType,
  recordId: string,
  instruction: string,
  /** The up-to cost the person saw on the button; the server requires it. */
  approveUsd: number,
) =>
  createAIJob({
    project_id: projectId,
    kind: "ai_edit",
    input: { record_type: recordType, record_id: recordId, instruction, approve_usd: approveUsd },
  });
