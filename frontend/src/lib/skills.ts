import { apiError } from "./apiError";
// S1.5 — save-time correction-signal capture.
//
// After a successful scope/estimate save the editor opens a small modal
// asking "Why did you make this edit?". The answer determines whether the
// diff feeds the auto-improvement loop:
//
//   ai_miss_*        → feeds_learning = true   (signals an AI mistake)
//   project_*, other → feeds_learning = false  (subjective / situational)
//
// Tagging is optional; "Skip" closes the modal without writing a signal.

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
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Skill registry (versioning admin view) ───────────────────────────

export interface SkillVersionRead {
  id: string;
  skill_id: string;
  version: string;
  owner_namespace: string;
  status: string;
  description: string | null;
  fingerprint: string | null;
  prompt_chars: number;
  approved_by: string | null;
  approved_at: string | null;
  approval_note: string | null;
  created_at: string;
  updated_at: string;
}

export interface SkillSummary {
  skill_id: string;
  active_version: SkillVersionRead | null;
  all_versions: SkillVersionRead[];
  run_count: number;
  edited_run_count: number;
  correction_signal_count: number;
  learning_signal_count: number;
}

export interface VersionPrompt {
  skill_id: string;
  version: string;
  status: string;
  fingerprint: string | null;
  prompt_template: string;
}

export function listSkills(): Promise<SkillSummary[]> {
  return api<SkillSummary[]>("/skills");
}

export function getVersionPrompt(
  skillId: string,
  versionId: string,
): Promise<VersionPrompt> {
  return api<VersionPrompt>(
    `/skills/${encodeURIComponent(skillId)}/versions/${encodeURIComponent(
      versionId,
    )}/prompt`,
  );
}

export function approveVersion(
  skillId: string,
  versionId: string,
  note?: string | null,
): Promise<SkillVersionRead> {
  return api<SkillVersionRead>(
    `/skills/${encodeURIComponent(skillId)}/versions/${encodeURIComponent(
      versionId,
    )}/approve`,
    { method: "POST", body: JSON.stringify({ note: note ?? null }) },
  );
}

export function rejectVersion(
  skillId: string,
  versionId: string,
  note?: string | null,
): Promise<SkillVersionRead> {
  return api<SkillVersionRead>(
    `/skills/${encodeURIComponent(skillId)}/versions/${encodeURIComponent(
      versionId,
    )}/reject`,
    { method: "POST", body: JSON.stringify({ note: note ?? null }) },
  );
}

export interface RunByArtifact {
  skill_run_id: string;
  skill_version_id: string;
  acceptance_status: string;
}

/**
 * Look up the most recent skill_run that produced a given artifact. The
 * artifact_marker is the key in AIJob.output we expect — `scope_id`,
 * `estimate_version_id`, `document_summary_id`. Returns null if no run
 * is linked (e.g. the user edited a manually-created scope, not an
 * AI-generated one).
 */
export async function getRunByArtifact(
  skillId: string,
  artifactMarker: string,
  artifactValue: string,
): Promise<RunByArtifact | null> {
  return api<RunByArtifact | null>(
    `/skill-runs/by-artifact/${encodeURIComponent(skillId)}/${encodeURIComponent(
      artifactMarker,
    )}/${encodeURIComponent(artifactValue)}`,
  );
}

export interface CorrectionResult {
  skill_run_id: string;
  signal_count: number;
  acceptance_status: string;
  feeds_learning: boolean;
}

/**
 * Record the diff between the run's raw_output and the user's
 * accepted_output, tagged with a semantic category (or null = untagged).
 */
export function postCorrections(
  skillRunId: string,
  accepted_output: Record<string, unknown>,
  semantic_category: string | null,
  user_note?: string | null,
): Promise<CorrectionResult> {
  return api<CorrectionResult>(`/skill-runs/${skillRunId}/corrections`, {
    method: "POST",
    body: JSON.stringify({
      accepted_output,
      semantic_category,
      user_note: user_note ?? null,
    }),
  });
}

// ── Tag taxonomy ─────────────────────────────────────────────────────
// MUST match KNOWN_CATEGORIES on the backend
// (app/services/skill_engine.py). Order here is the display order in
// the modal.

export type FeedbackCategory =
  | "ai_miss_missed_item"
  | "ai_miss_wrong_qty_unit_pricing"
  | "ai_miss_wrong_category"
  | "ai_miss_doesnt_apply"
  | "project_owner_preference"
  | "project_site_condition"
  | "project_code_permit_nuance"
  | "other";

export interface TagOption {
  value: FeedbackCategory;
  label: string;
  hint: string;
  group: "ai_miss" | "project_specific" | "other";
}

export const TAG_OPTIONS: TagOption[] = [
  // AI miss — these feed the candidate generator
  {
    value: "ai_miss_missed_item",
    label: "AI missed an item",
    hint: "Something the project actually needs that the AI didn't include.",
    group: "ai_miss",
  },
  {
    value: "ai_miss_wrong_qty_unit_pricing",
    label: "Wrong quantity, unit, or pricing",
    hint: "The AI got the math or the unit wrong.",
    group: "ai_miss",
  },
  {
    value: "ai_miss_wrong_category",
    label: "Wrong category / misplaced",
    hint: "Item exists but is in the wrong section / category.",
    group: "ai_miss",
  },
  {
    value: "ai_miss_doesnt_apply",
    label: "Included something that doesn't apply",
    hint: "AI added an item that isn't part of this project.",
    group: "ai_miss",
  },
  // Project-specific — recorded but don't feed learning
  {
    value: "project_owner_preference",
    label: "Owner preference / atypical",
    hint: "Edit is correct for this project but not a general pattern.",
    group: "project_specific",
  },
  {
    value: "project_site_condition",
    label: "Site condition the AI couldn't know",
    hint: "Field condition / discovery the AI had no way to see.",
    group: "project_specific",
  },
  {
    value: "project_code_permit_nuance",
    label: "Local code / permit nuance",
    hint: "AHJ-specific or jurisdictional carve-out.",
    group: "project_specific",
  },
  {
    value: "other",
    label: "Other",
    hint: "Use the note box to describe.",
    group: "other",
  },
];
