import type { JobProgressSummary, JobStep } from "@/components/JobProgress";
import { apiError } from "./apiError";
// AI job API wrappers + SSE stream helper. Routes through `/api/backend/*` so
// the NextAuth cookie reaches the backend (cross-origin is blocked).

const BASE = "/api/backend";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) throw await apiError(res);
  return res.json() as Promise<T>;
}

//: Hand-maintained mirror of backend AI_JOB_KINDS. It has drifted three times,
//: and each time the symptom was a 422 on job creation rather than a type
//: error — the drift is invisible until something is actually submitted. If a
//: new kind fails to create, check here first.
export type AIJobKind =
  | "scope_analysis"
  | "estimate_generation"
  | "email_draft"
  | "ocr"
  | "document_summary"
  | "cad_takeoff"
  | "estimate_full"
  | "presentation"
  | "scope_from_estimate"
  | "schedule_from_estimate"
  | "change_order_generation"
  | "ai_edit";
export type AIJobStatus = "queued" | "running" | "done" | "error";

export interface AIJobImageInput {
  media_type: string;
  data: string; // base64 (no data: prefix)
  caption?: string;
}

export interface AIJobScopeInput {
  images?: AIJobImageInput[];
  notes?: string;
  project_metadata?: {
    address?: string | null;
    building_age?: number | null;
    building_type?: string | null;
    sqft?: number | null;
    project_type?: string | null;
    name?: string | null;
  };
  // estimate_generation / email_draft / ocr branches can pass arbitrary keys.
  [key: string]: unknown;
}

export interface CreateAIJobBody {
  project_id: string;
  kind: AIJobKind;
  input: AIJobScopeInput;
}

export interface AIJobRead {
  id: string;
  project_id: string;
  kind: AIJobKind;
  status: AIJobStatus;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;
  attempts: number;
  created_at: string;
  updated_at: string;
  /** What the job is doing, step by step (estimate_full: one per trade). */
  steps?: JobStep[] | null;
  progress?: JobProgressSummary | null;
}

export interface AIJobStreamEvent {
  id: string;
  status: AIJobStatus;
  kind: AIJobKind;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;
  attempts: number;
  error: string | null;
  output: Record<string, unknown> | null;
}

export function createAIJob(body: CreateAIJobBody): Promise<AIJobRead> {
  return api(`/ai-jobs`, { method: "POST", body: JSON.stringify(body) });
}

/** What a scope, estimate or describe-a-change job will cost at most. Shown on
 *  the button that starts it; pressing the button approves it, and the server
 *  refuses those jobs without an approved amount (Amir, 2026-10-15). */
export interface JobCost {
  kind: string;
  usd: number;
  how: string;
}

export function getJobCost(projectId: string, kind: AIJobKind): Promise<JobCost> {
  return api(`/ai-jobs/cost-estimate?project_id=${encodeURIComponent(projectId)}&kind=${encodeURIComponent(kind)}`);
}

/** "up to $0.50" — the wording Ask and Prepare use. */
export const upTo = (usd: number) => `up to $${usd.toFixed(2)}`;

export function getAIJob(id: string): Promise<AIJobRead> {
  return api(`/ai-jobs/${id}`);
}

/** Jobs for a project, newest first. Used to re-attach to a run in progress
 *  after a reload — the server holds the state, not the browser. */
export function listAIJobs(projectId: string): Promise<AIJobRead[]> {
  return api(`/ai-jobs?project_id=${encodeURIComponent(projectId)}`);
}

/** Open an SSE stream to the AI-job status feed via the same-origin proxy. */
export function streamAIJob(jobId: string): EventSource {
  return new EventSource(`${BASE}/ai-jobs/${encodeURIComponent(jobId)}/stream`);
}
