const BASE = "/api/backend";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json())?.detail;
    } catch {
      /* body was not JSON */
    }
    const err = new Error(
      typeof detail === "string"
        ? detail
        : (detail as { message?: string })?.message ?? `Request failed (${res.status})`,
    );
    (err as Error & { detail?: unknown }).detail = detail;
    throw err;
  }
  return res.json() as Promise<T>;
}

export type AccuracyGrade = {
  grade: string;
  headline: string;
  coverage_pct: number;
  measured_divisions: string[];
  allowance_divisions: string[];
  next_uploads: {
    discipline: string;
    have: string;
    ask: string;
    unlocks: string;
    divisions: string[];
    budget_critical: boolean;
  }[];
  warnings: string[];
  disciplines: Record<string, { source: string; divisions: string[]; gives: string }>;
};

export type UnresolvedQuantity = {
  id: string;
  discipline: string;
  quantity_type: string;
  value: number | null;
  unit: string;
  source: string;
  notes: string | null;
  /** Which drawing the number came off, so it can be shown beside it. */
  source_document_id: string | null;
};

export type SheetViewMeta = {
  index: number;
  title: string | null;
  source: string;
  width_ft: number;
  height_ft: number;
  included: boolean;
};

export type SheetView = {
  filename: string;
  highlight: string | null;
  svg: string | null;
  view: SheetViewMeta | null;
  /** Every view on the sheet. A view the takeoff EXCLUDED is existing work or
   *  context — drawable, but not what the numbers describe. */
  views: SheetViewMeta[];
  view_count: number;
  /** The individual elements counted, in the order they are numbered on the
   *  drawing. The block name is what makes a count checkable — "Overhead -
   *  18' x 8'" is a garage door, and a reviewer can see that it should be. */
  marks: { x: number; y: number; name: string }[];
  rooms: { centerline_sf: number; net_sf: number }[];
  warnings: string[];
};

export type AnalysisResponse = {
  pack: {
    id: string;
    version_number: number;
    status: string;
    payload: {
      sheets_read?: number;
      sheets?: {
        filename: string;
        error?: string;
        skipped?: string;
        /** Sheet type read from the name: floor_plan, section, site_plan… */
        role?: string | null;
        /** Which building the sheet was grouped into. Roof and perimeter
         *  resolve per building, so a mis-grouping changes the numbers. */
        building?: string | null;
        /** Why a sheet that read fine still contributes no quantities. */
        not_measured?: string | null;
        quantities_written?: number;
        views?: number;
        warnings?: string[];
      }[];
      set_audit?: { complete?: boolean; findings?: string[] } | null;
    };
    approved_at: string | null;
    created_at: string;
  } | null;
  gate: {
    open: boolean;
    takeoff_mode: string;
    accuracy: string;
    grade: AccuracyGrade | null;
    counts: Record<string, number>;
    unresolved: UnresolvedQuantity[];
  };
  can_approve: boolean;
};

export function getAnalysis(projectId: string) {
  return api<AnalysisResponse>(`/projects/${projectId}/drawing-analysis`);
}

export function buildAnalysis(projectId: string) {
  return api<AnalysisResponse>(`/projects/${projectId}/drawing-analysis`, {
    method: "POST",
  });
}

export function approveAnalysis(projectId: string, note?: string) {
  return api<AnalysisResponse>(`/projects/${projectId}/drawing-analysis/approve`, {
    method: "POST",
    body: JSON.stringify({ note: note ?? null }),
  });
}

/** confirm | correct | exclude, one quantity at a time. */
export function reviewQuantity(
  overrideId: string,
  action: "confirm" | "correct" | "exclude",
  value?: number,
  notes?: string,
) {
  return api<unknown>(`/quantity-overrides/${overrideId}/review`, {
    method: "POST",
    body: JSON.stringify({ action, value: value ?? null, notes: notes ?? null }),
  });
}

/** The drawing behind a quantity, with that quantity's source marked. */
export function getSheetView(
  projectId: string,
  documentId: string,
  opts?: { view?: number; highlight?: string | null },
) {
  const q = new URLSearchParams();
  if (opts?.view != null) q.set("view", String(opts.view));
  if (opts?.highlight) q.set("highlight", opts.highlight);
  return api<SheetView>(
    `/projects/${projectId}/drawing-analysis/sheet/${documentId}/view?${q}`,
  );
}
