import { apiError } from "./apiError";
// Projects — list / get / create.

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

export interface DiscussionPoint {
  title: string;
  body: string;
  computed?: string;
}

export type ProjectStatus =
  | "lead"
  | "preconstruction"
  | "in_progress"
  | "closeout"
  | "archived";

export const PROJECT_STATUSES: ProjectStatus[] = [
  "lead",
  "preconstruction",
  "in_progress",
  "closeout",
  "archived",
];

export const STATUS_LABELS: Record<ProjectStatus, string> = {
  lead: "Lead",
  preconstruction: "Preconstruction",
  in_progress: "In progress",
  closeout: "Closeout",
  archived: "Archived",
};

/**
 * A fixed set, not free text. The type selects which cost-division set an
 * estimate is organised into — residential resolves to BRIO's 18 numbered
 * divisions, commercial to CSI MasterFormat — so it has to be exact.
 *
 * Nullable on read: 19 existing projects are deliberately unclassified because
 * their names gave no reliable signal, and guessing would silently mis-seed
 * every future estimate on them.
 */
export type ProjectType =
  | "residential_new"
  | "residential_reno"
  | "commercial_ti"
  | "commercial_new";

export const PROJECT_TYPES: ProjectType[] = [
  "residential_new",
  "residential_reno",
  "commercial_ti",
  "commercial_new",
];

export const PROJECT_TYPE_LABELS: Record<ProjectType, string> = {
  residential_new: "Residential — new build",
  residential_reno: "Residential — renovation",
  commercial_ti: "Commercial — tenant improvement",
  commercial_new: "Commercial — new build",
};

/** Answered on the project since migration 0066. NULL = not decided yet. */
export type FinishLevel = "builder_grade" | "mid" | "high";
export type DeliveredAs = "finished" | "shell" | "part_shell";
/** What was decided about a discipline with no drawings ("all" = no drawings at all). */
export type DrawingDecision = "allowance" | "not_in_scope";

export const FINISH_LEVEL_LABELS: Record<FinishLevel, string> = {
  builder_grade: "Builder-grade",
  mid: "Mid",
  high: "High",
};

export const DELIVERED_AS_LABELS: Record<DeliveredAs, string> = {
  finished: "Finished",
  shell: "Shell",
  part_shell: "Part shell, part finished",
};

export interface ProjectRead {
  id: string;
  slug: string;
  name: string;
  address: string | null;
  summary: string | null;
  type: string | null;
  status: string;
  phase: string | null;
  client_id: string | null;
  building_age: number | null;
  building_type: string | null;
  sqft: number | null;
  finish_level: FinishLevel | null;
  delivered_as: DeliveredAs | null;
  drawing_decisions: Record<string, DrawingDecision>;
  google_drive_url: string | null;
  index_documents: boolean;
  home_warranty_required: boolean;
  pre_archive_status: string | null;
  discussion_points: DiscussionPoint[];
  created_at: string;
  updated_at: string;
}

export function listProjects(): Promise<ProjectRead[]> {
  return api("/projects");
}

/** Archive a project (retains its current status for a later restore). */
export function archiveProject(id: string): Promise<ProjectRead> {
  return api(`/projects/${id}/archive`, { method: "POST" });
}

/** Unarchive — restores the status held before archiving (else 'lead'). */
export function unarchiveProject(id: string): Promise<ProjectRead> {
  return api(`/projects/${id}/unarchive`, { method: "POST" });
}

export function getProject(id: string): Promise<ProjectRead> {
  return api(`/projects/${id}`);
}

/** Send the client a curated update email (new photos / milestone / note). */
export function notifyClient(
  id: string,
  kind: "photos" | "milestone" | "update",
  message?: string,
): Promise<{ ok: boolean; recipients: number }> {
  return api(`/projects/${id}/notify-client`, {
    method: "POST",
    body: JSON.stringify({ kind, message }),
  });
}

export function createProject(body: {
  name: string;
  address?: string;
  // Both required since 2026-09-27 — see ProjectType, and building_age drives
  // hazmat exposure which cannot be reconstructed after the fact.
  type: ProjectType;
  status?: ProjectStatus;
  building_age: number;
  building_type?: string;
  sqft?: number;
  google_drive_url?: string;
}): Promise<ProjectRead> {
  return api("/projects", { method: "POST", body: JSON.stringify(body) });
}

/**
 * Partial-update a project. Only fields present in `body` are written;
 * pass `null` to explicitly clear a value, omit to leave alone.
 */
export function updateProject(
  id: string,
  body: Partial<{
    name: string;
    address: string | null;
    summary: string | null;
    type: string | null;
    status: ProjectStatus;
    building_age: number | null;
    building_type: string | null;
    sqft: number | null;
    finish_level: FinishLevel | null;
    delivered_as: DeliveredAs | null;
    drawing_decisions: Record<string, DrawingDecision>;
    google_drive_url: string | null;
    index_documents: boolean;
    home_warranty_required: boolean;
    discussion_points: DiscussionPoint[];
  }>,
): Promise<ProjectRead> {
  return api(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

/** AI-draft client-discussion points from project context. Returns drafts for
 *  review — does NOT save them. */
export function suggestDiscussionPoints(
  id: string,
): Promise<{ points: DiscussionPoint[] }> {
  return api(`/projects/${id}/discussion-points/suggest`, { method: "POST" });
}
