import { apiError } from "./apiError";
// Drawing-analysis + quantity-overrides API client.

const BASE = "/api/backend";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) throw await apiError(res);
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export interface DrawingPageMeta {
  page: number;
  width: number;
  height: number;
  rotation: number;
  text_run_count: number;
  vector_path_count: number;
  dimension_count: number;
  image_count: number;
  /** The sheet's plan is a pasted picture: nothing can be detected on it. */
  raster_plan?: boolean;
  raster_regions?: [number, number, number, number][];
}

export interface PageScale {
  page: number;
  scale_label: string | null;
  pdf_units_per_real_foot: number | null;
  /** "high" | "medium" | "measured" (from printed dimensions) | "low" */
  confidence: string;
  /** What the sheet's printed dimensions measure at, when enough agree. */
  measured_ppf?: number | null;
  measured_agreeing?: number;
  measured_readings?: number;
}

export interface PathInCandidate {
  bbox: [number, number, number, number];
  length: number;
}

export interface LinearCandidate {
  page: number;
  classification: string;
  width_bucket: string;
  orthogonal: boolean;
  path_count: number;
  stroke_width: number;
  total_length_pdf_units: number;
  bbox: [number, number, number, number];
  /** Top-N member paths (by length). Capped server-side; `path_count` is the
   * full raw count so the UI can show "showing N of M strokes". */
  paths: PathInCandidate[];
}

export interface LinearSummaryRow {
  page: number;
  classification: string;
  width_bucket: string;
  orthogonal: boolean;
  path_count: number;
  stroke_width: number;
  pdf_units: number;
  real_feet: number | null;
  scale_confidence: string;
}

export interface DimensionText {
  text: string;
  bbox: [number, number, number, number];
  page: number;
  font_size: number;
}

export interface DrawingAnalysis {
  id: string;
  document_id: string;
  content_hash: string;
  is_vector: boolean;
  page_count: number;
  pages: DrawingPageMeta[];
  candidates: LinearCandidate[];
  scales: PageScale[];
  dimensions: DimensionText[];
  linear_summary: LinearSummaryRow[];
  stats: {
    text_runs: number;
    raw_paths: number;
    dimensions: number;
    candidates: number;
    scales_with_label: number;
  };
}

/** Answer while the background worker is still reading the drawing (202). */
export interface DrawingAnalysisPending {
  analysis_status: "analysing" | "failed";
  job_id: string;
  error?: string;
}

export function isAnalysisPending(
  r: DrawingAnalysis | DrawingAnalysisPending,
): r is DrawingAnalysisPending {
  return "analysis_status" in r;
}

/** The stored analysis, or a pending answer: the server never parses inside
 * the request any more (it took the API down, 2026-10-26), so callers poll. */
export function getDrawingAnalysis(
  documentId: string,
  force = false,
): Promise<DrawingAnalysis | DrawingAnalysisPending> {
  const q = force ? "?force=true" : "";
  return api(`/drawings/${documentId}/analysis${q}`);
}

// ── quantity overrides ───────────────────────────────────────────

export interface QuantityOverride {
  id: string;
  project_id: string;
  discipline: string;
  quantity_type: string;
  value: number | null;
  unit: string;
  source: string;
  source_document_id: string | null;
  source_page: number | null;
  source_candidate_key: string | null;
  notes: string | null;
}

export function listQuantityOverrides(
  projectId: string,
  discipline?: string,
): Promise<QuantityOverride[]> {
  const q = new URLSearchParams({ project_id: projectId });
  if (discipline) q.set("discipline", discipline);
  return api(`/quantity-overrides?${q}`);
}

export function upsertQuantityOverride(
  body: Omit<QuantityOverride, "id">,
): Promise<QuantityOverride> {
  return api(`/quantity-overrides`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteQuantityOverride(overrideId: string): Promise<void> {
  return api(`/quantity-overrides/${overrideId}`, { method: "DELETE" });
}

/** Stable identifier for a LinearCandidate — used as the upsert key. */
export { candidateKey } from "./candidateKey";

// ── per-page scale overrides ─────────────────────────────────────
// Manual scale entry + two-point calibration, for drawings where
// auto-detection finds no scale (vectorized title block / metric set).

export interface ScaleOverride {
  id: string;
  document_id: string;
  page: number;
  pdf_units_per_real_foot: number;
  scale_label: string | null;
  source: string; // 'manual_select' | 'calibrated'
}

/** Named scales offered in the per-page dropdown. Labels match the formats
 * the backend's `units_per_foot_from_label` understands (imperial
 * `X" = 1'-0"` and metric `1:NN`). */
export { NAMED_SCALES } from "./scaleSelect";

export function listScaleOverrides(documentId: string): Promise<ScaleOverride[]> {
  return api(`/drawings/${documentId}/scale-overrides`);
}

export function upsertScaleOverride(
  documentId: string,
  page: number,
  body: {
    scale_label?: string | null;
    pdf_units_per_real_foot?: number | null;
    source: string;
  },
): Promise<ScaleOverride> {
  return api(`/drawings/${documentId}/scale-overrides/${page}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteScaleOverride(
  documentId: string,
  page: number,
): Promise<void> {
  return api(`/drawings/${documentId}/scale-overrides/${page}`, {
    method: "DELETE",
  });
}
