import { apiError } from "./apiError";
// Rate library (Track 2 Phase 6) — client API for the Rate Library admin page.
// Uses the /api/backend proxy so calls carry the NextAuth session.

const BASE = "/api/backend";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) throw await apiError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export type SourceType =
  | "own"
  | "subcontract_derived"
  | "published"
  | "composed"
  | "market";

export interface Provenance {
  source_type: SourceType;
  confidence: string;
  entry_id: string;
  project_type: string;
  source_ref: string | null;
  as_of_date: string | null;
}

/** How far a stored price has likely moved since its date (services/price_drift.py). */
export interface Drift {
  division: string;
  latest: string;
  since: string | null;
  pct: number | null;
  stale: boolean;
  reason: string | null;
}

/** Shared vocabulary fields every library row carries (phase 1). */
export interface VocabFields {
  trade_key?: string | null;
  trade_label?: string | null;
  division?: string | null;
}

export interface AssemblyBest extends VocabFields {
  status?: string;
  drift?: Drift | null;
  code: string;
  name: string;
  trade: string;
  category: string | null;
  unit: string;
  project_type: string;
  unit_rate: number | null;
  provenance: Provenance | null;
}

export interface RateEntry {
  id: string;
  unit_rate: number;
  unit: string;
  source_type: SourceType;
  confidence: string;
  project_type: string;
  source_ref: string | null;
  as_of_date: string | null;
  is_active: boolean;
  payload: Record<string, unknown>;
  created_by: string;
  created_at: string | null;
}

export interface AssemblyDetail {
  used_on_estimates?: number | null;
  code: string;
  name: string;
  trade: string;
  category: string | null;
  unit: string;
  project_type: string;
  scope_notes: string | null;
  status: string;
  best_entry_id: string | null;
  entries: RateEntry[];
}

export interface MaterialRate extends VocabFields {
  category?: string | null;
  measure_unit?: string | null;
  coverage?: number | null;
  cost_per_measure?: { usd: number; unit: string } | null;
  drift?: Drift | null;
  /** Supply-only cost band; unit_cost is the mid. Absent on yard-survey rows. */
  band?: { low: number; mid: number; high: number } | null;
  payload?: Record<string, unknown>;
  id: string;
  name: string;
  sku: string | null;
  trade: string | null;
  unit: string;
  unit_cost: number;
  source: string;
  supplier: string | null;
  project_type: string;
  waste_pct: number | null;
  as_of_date: string | null;
  source_ref: string | null;
}

export interface LabourRate extends VocabFields {
  id: string;
  trade: string;
  region: string;
  project_type: string;
  source: string;
  base_rate: number;
  discount_pct: number;
  effective_rate: number;
  as_of_date: string | null;
  source_ref: string | null;
  /** BRIO billable $/hr band — comparison only, never a cost. */
  billable_band?: { low: number; mid: number; high: number } | null;
  payload?: Record<string, unknown>;
}

export interface PriceResult {
  priced: boolean;
  assembly_code: string;
  unit?: string;
  unit_rate?: number;
  quantity?: number;
  amount?: number;
  provenance?: Provenance;
}

const q = (pt: string) => (pt && pt !== "any" ? `?project_type=${pt}` : "");

export const listAssemblies = (projectType = "any", status = "active") =>
  api<AssemblyBest[]>(
    `/rates/assemblies?project_type=${encodeURIComponent(projectType)}&status=${encodeURIComponent(status)}`,
  );

export const setAssemblyStatus = (code: string, status: "active" | "retired" | "draft" | "held") =>
  api<{ code: string; status: string; was: string }>(`/rates/assemblies/${encodeURIComponent(code)}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });

export const getAssembly = (code: string, projectType = "any") =>
  api<AssemblyDetail>(
    `/rates/assemblies/${encodeURIComponent(code)}${q(projectType)}`,
  );

export const priceAssembly = (body: {
  assembly_code: string;
  quantity: number;
  project_type: string;
}) =>
  api<PriceResult>(`/rates/price`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const listMaterials = (trade?: string) =>
  api<MaterialRate[]>(`/rates/materials${trade ? `?trade=${trade}` : ""}`);

export const listLabour = (trade?: string) =>
  api<LabourRate[]>(`/rates/labour${trade ? `?trade=${trade}` : ""}`);

export const createAssembly = (body: {
  code: string;
  name: string;
  trade: string;
  unit: string;
  category?: string | null;
  project_type?: string;
  description?: string | null;
  scope_notes?: string | null;
}) =>
  api<{ id: string; code: string }>(`/rates/assemblies`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const createEntry = (body: {
  assembly_code: string;
  unit_rate: number;
  source_type: SourceType;
  confidence?: string;
  project_type?: string;
  source_ref?: string | null;
  as_of_date?: string | null;
}) =>
  api<{ id: string; superseded: number }>(`/rates/entries`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const createMaterial = (body: {
  name: string;
  unit: string;
  unit_cost: number;
  source?: string;
  trade?: string | null;
  supplier?: string | null;
  project_type?: string;
  waste_pct?: number | null;
  source_ref?: string | null;
  sku?: string | null;
  measure_unit?: string | null;
  coverage?: number | null;
  as_of_date?: string | null;
}) =>
  api<{ id: string; name: string }>(`/rates/materials`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const createLabour = (body: {
  trade: string;
  base_rate: number;
  source?: string;
  project_type?: string;
  discount_pct?: number | null;
  effective_rate?: number | null;
  source_ref?: string | null;
}) =>
  api<{ id: string; trade: string; effective_rate: number }>(`/rates/labour`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// Presentation helpers ------------------------------------------------

export const SOURCE_LABEL: Record<SourceType, string> = {
  own: "Your rate",
  subcontract_derived: "Subcontract-derived",
  published: "Published",
  composed: "Composed",
  market: "Market",
};

// Tailwind classes for the source badge — trust order, greens→amber.
export const SOURCE_BADGE: Record<SourceType, string> = {
  own: "bg-emerald-200 text-emerald-900 border-emerald-300",
  subcontract_derived: "bg-emerald-100 text-emerald-800 border-emerald-200",
  published: "bg-sky-100 text-sky-800 border-sky-200",
  composed: "bg-amber-100 text-amber-800 border-amber-200",
  market: "bg-slate-100 text-slate-700 border-slate-300",
};

// ── Phase 3: review fixes write back to the library ─────────────────────────

/** One library change a save would make (services/rate_writeback.proposals). */
export interface RateProposal {
  key: string;
  line: string;
  category: string;
  /** Ticked by default for a linked line; an allowance becoming a rate starts unticked. */
  default: boolean;
  kind: "assembly" | "material" | "labour" | "new";
  code?: string;
  unit: string;
  old_rate?: number;
  new_rate: number;
  material_per_unit?: number;
  hours_per_unit?: number | null;
}

/** The save step's answer for one proposal. `reason` is required to keep a linked rate to this job. */
export interface RateDecision {
  key: string;
  write: boolean;
  reason?: string;
}

export const previewRateWriteback = (
  body: { document: Record<string, unknown> } | { ai_edit_job_id: string; accept_paths: string[] },
) => api<{ proposals: RateProposal[] }>("/rates/writeback/preview", { method: "POST", body: JSON.stringify(body) });


// ── Supplier catalogue and price-sheet imports (phase 4) ────────────────────

export interface CatalogueLine extends VocabFields {
  id: string;
  supplier: string;
  sku: string;
  description: string;
  supplier_group: string | null;
  supplier_category: string | null;
  raw_unit: string;
  raw_price: number;
  unit: string | null;
  unit_cost: number | null;
  as_of_date: string | null;
  drift: Drift | null;
}

export const searchCatalogue = (p: { q?: string; trade?: string; limit?: number; offset?: number }) => {
  const qs = new URLSearchParams();
  if (p.q) qs.set("q", p.q);
  if (p.trade) qs.set("trade", p.trade);
  qs.set("limit", String(p.limit ?? 50));
  qs.set("offset", String(p.offset ?? 0));
  return api<{ total: number; items: CatalogueLine[] }>(`/rates/catalogue?${qs}`);
};

export type ImportField = "sku" | "description" | "unit" | "price" | "category";

export interface ImportRow {
  line: number;
  sku: string;
  description: string;
  raw_unit: string;
  raw_price: number | string;
  unit?: string;
  unit_cost?: number;
  bucket: "new" | "changed" | "unchanged" | "problem";
  problem?: string;
  old_price?: number;
  change_pct?: number | null;
  big_move?: boolean;
}

export interface ImportMaterialMatch {
  material_id: string;
  name: string;
  sku: string;
  unit: string;
  old_cost: number;
  new_cost: number | null;
  default: boolean;
  note: string | null;
}

export interface PriceImport {
  id: string;
  supplier: string;
  filename: string | null;
  status: "staged" | "applied" | "undone" | "discarded";
  column_map: Partial<Record<ImportField, number>>;
  summary: {
    counts?: Record<"new" | "changed" | "unchanged" | "problem" | "big_move", number>;
    materials?: ImportMaterialMatch[];
    headers?: string[];
    total?: number;
    applied?: Record<string, number>;
  };
  created_by: string;
  created_at: string | null;
  applied_by: string | null;
  applied_at: string | null;
  undone_by: string | null;
  undone_at: string | null;
  rows?: ImportRow[];
  rows_total?: number;
}

/** Upload + check. Returns a staged import, or `needs_map` with the headers when the columns can't be guessed. */
export async function stageImport(
  file: File,
  supplier: string,
  columnMap?: Partial<Record<ImportField, number>>,
): Promise<(PriceImport & { headers: string[]; sample: string[][] }) | { status: "needs_map"; headers: string[]; sample: string[][]; column_map: Partial<Record<ImportField, number>>; detail: string }> {
  const fd = new FormData();
  fd.set("file", file);
  fd.set("supplier", supplier);
  if (columnMap) fd.set("column_map", JSON.stringify(columnMap));
  const res = await fetch(`${BASE}/rates/imports`, { method: "POST", body: fd, cache: "no-store" });
  if (!res.ok) throw await apiError(res);
  return res.json();
}

export const listImports = () => api<PriceImport[]>("/rates/imports");
export const getImport = (id: string, bucket?: string) =>
  api<PriceImport>(`/rates/imports/${id}${bucket ? `?bucket=${bucket}` : ""}`);
export const applyImport = (id: string, materialIds: string[]) =>
  api<PriceImport & { applied: Record<string, number> }>(`/rates/imports/${id}/apply`, {
    method: "POST",
    body: JSON.stringify({ material_ids: materialIds }),
  });
export const undoImport = (id: string) =>
  api<PriceImport & { undone: Record<string, number> }>(`/rates/imports/${id}/undo`, { method: "POST" });
