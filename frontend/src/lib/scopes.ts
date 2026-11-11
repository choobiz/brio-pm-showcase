import { apiError } from "./apiError";
// Client-safe scope API wrappers. Routes through the same-origin proxy at
// `/api/backend/*` so the NextAuth cookie traverses (cross-origin cookies to
// the backend's nip.io domain are blocked by the browser).

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

export interface ScopeCategory {
  name: string;
  items: string[];
}

export interface ScopeMaterial {
  item: string;
  spec?: string;
  notes?: string;
}

// The model emits a fixed JSON shape (see `build_scope_analysis_messages`
// in backend/app/services/ai.py). Optional fields are kept loose so we don't
// crash when the model omits an array — the editor always normalizes.
export interface ScopePayload {
  reference?: string;
  date?: string;
  summary?: string;
  categories?: ScopeCategory[];
  materials?: ScopeMaterial[];
  potentialIssues?: string[];
  accessConstraints?: string[];
  permits?: string[];
  assumptions?: string[];
  exclusions?: string[];
  /** Set when this version was made by describing a change to another. */
  _revision?: { from_version: number | null; from_id: string; instruction: string };
  [key: string]: unknown;
}

// Provenance — what Keystone was given to produce the scope. Set by the backend
// from the originating AIJob; absent for hand-created / imported scopes.
export interface ScopeSourceImage {
  caption?: string | null;
  media_type?: string | null;
}

export interface ScopeSource {
  notes?: string | null;
  images: ScopeSourceImage[];
  image_count: number;
  project_metadata: Record<string, unknown>;
  model?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cost_usd?: string | null;
  generated_at?: string | null;
  /** The library files ticked for the run, each marked read or not. */
  documents?: ScopeSourceDocument[];
}

export interface ScopeSourceDocument {
  id: string;
  filename: string | null;
  read: boolean;
  reason: string | null;
}

export interface ScopeRead {
  id: string;
  project_id: string;
  version: number;
  payload: ScopePayload;
  ai_job_id?: string | null;
  source?: ScopeSource | null;
  /** The project's name and address from its own record (every version). */
  project?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export function listScopes(projectId: string): Promise<ScopeRead[]> {
  return api(`/scopes?project_id=${encodeURIComponent(projectId)}`);
}

export function getScope(id: string): Promise<ScopeRead> {
  return api(`/scopes/${id}`);
}

export function updateScope(id: string, payload: ScopePayload): Promise<ScopeRead> {
  return api(`/scopes/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ payload }),
  });
}

export interface ScopePdfResult {
  document_id: string;
  filename: string;
  size_bytes: number;
  download_url: string;
}

// Renders the scope to a branded PDF, stores it in R2 as a Document, and
// returns a short-lived download URL.
export function generateScopePdf(id: string): Promise<ScopePdfResult> {
  return api(`/scopes/${id}/pdf`, { method: "POST" });
}

// Renders the project-wide unified Scope of Work — a single industry-
// standard SOW document. Per-trade scopes are merged into project-level
// sections (project info → overview → numbered scope-of-work → materials
// → permits → site logistics → assumptions → exclusions → risk
// considerations). Same storage / response shape as the per-scope variant.
export function generateFullScopePdf(
  projectId: string,
): Promise<ScopePdfResult> {
  return api(
    `/scopes/full-pdf?project_id=${encodeURIComponent(projectId)}`,
    { method: "POST" },
  );
}

// Client-facing edition was moved to the estimate side (Investment
// Proposal) — see workbench `/estimate-client-pdf` route. The scope
// document family now keeps only the per-scope and the unified
// whole-project Scope of Work.
