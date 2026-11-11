/**
 * The Takeoff page: read the drawings, check each measurement, sign the set off.
 *
 * Thin wrappers over the existing drawing-analysis and quantity-override routes,
 * through workspaceApi so failures arrive as ApiError for <WorkspaceError>.
 */
import type { AnalysisResponse } from "@/lib/drawingAnalysis";
import { workspaceGet, workspacePost } from "@/lib/workspaceApi";

export type MeasurementStatus = "candidate" | "confirmed" | "corrected" | "excluded";

/** One quantity_overrides row — a single measurement a person can check. */
export type Measurement = {
  id: string;
  discipline: string;
  quantity_type: string;
  value: number | null;
  unit: string;
  source: string;
  source_document_id: string | null;
  source_page: number | null;
  notes: string | null;
  status: MeasurementStatus;
  /** What the machine read, kept beside a person's correction. */
  machine_value: number | null;
  confirmed_at: string | null;
  source_revision_label: string | null;
};

export function getTakeoff(projectId: string) {
  return workspaceGet<AnalysisResponse>(`/projects/${projectId}/drawing-analysis`);
}

/** Reads every CAD file and writes a new draft. Synchronous; can take a minute. */
export function readDrawings(projectId: string) {
  return workspacePost<AnalysisResponse>(`/projects/${projectId}/drawing-analysis`);
}

export function signOff(projectId: string, note?: string) {
  return workspacePost<AnalysisResponse>(`/projects/${projectId}/drawing-analysis/approve`, {
    note: note ?? null,
  });
}

export function listMeasurements(projectId: string) {
  return workspaceGet<Measurement[]>(`/quantity-overrides?project_id=${encodeURIComponent(projectId)}`);
}

export function checkMeasurement(id: string, action: "confirm" | "correct" | "exclude", value?: number) {
  return workspacePost<Measurement>(`/quantity-overrides/${id}/review`, {
    action,
    value: value ?? null,
  });
}

export function checkMeasurements(projectId: string, ids: string[], action: "confirm" | "exclude") {
  return workspacePost<{ updated: number }>(`/quantity-overrides/review-bulk`, {
    project_id: projectId,
    ids,
    action,
  });
}

const LABELS: Record<string, string> = {
  door_count: "Doors",
  window_count: "Windows",
  plumbing_fixture_count: "Plumbing fixtures",
  floor_area_sf: "Floor area",
  roof_area_sf: "Roof area",
  perimeter_lf: "Perimeter",
};

/** "wall_lf_exterior" → "Wall length · exterior". */
export function measurementLabel(quantityType: string): string {
  if (LABELS[quantityType]) return LABELS[quantityType];
  const wall = quantityType.match(/^wall_lf_(.+)$/);
  // The CAD layer it was read from, as the drawing names it (A-WALL, I-WALL),
  // not as a lower-case code ("a-wall") that reads like jargon.
  if (wall) return `Wall length · ${wall[1].replace(/_/g, " ").toUpperCase()} layer`;
  const words = quantityType.replace(/_(sf|lf|ea|count)$/, "").replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export const STATUS_LABEL: Record<MeasurementStatus, string> = {
  candidate: "Not checked",
  confirmed: "Correct",
  corrected: "Fixed",
  excluded: "Not counted",
};
