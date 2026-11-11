import { apiError } from "./apiError";
// Track D — estimates: list / get / edit / PDF.
// Generation goes through the AI worker (createAIJob 'estimate_generation').

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

export type EstimateDetail =
  | "summary"
  | "standard"
  | "detailed"
  | "fully_itemized";

export const DETAIL_LEVELS: { value: EstimateDetail; label: string }[] = [
  { value: "summary", label: "Summary — category totals only" },
  { value: "standard", label: "Standard — category + description" },
  { value: "detailed", label: "Detailed — labor/material split" },
  { value: "fully_itemized", label: "Fully Itemized — every task" },
];

export interface EstimateItem {
  name: string;
  labor?: number;
  materials?: number;
  qty?: number;
  unitCost?: number;
  laborHours?: number;
}

export interface EstimateCategory {
  name: string;
  description?: string;
  total?: number;
  items?: EstimateItem[];
}

export interface PaymentMilestone {
  milestone: string;
  percentage?: number;
  description?: string;
}

// The model emits this shape; the worker folds in overhead/profit/allowances.
export interface EstimatePayload {
  reference?: string;
  date?: string;
  detailLevel?: string;
  categories?: EstimateCategory[];
  overhead?: number;
  profit?: number;
  allowances?: number;
  assumptions?: string[];
  exclusions?: string[];
  paymentSchedule?: PaymentMilestone[];
  validDays?: number;
  notes?: string;
  [key: string]: unknown;
}

export interface EstimateRead {
  id: string;
  project_id: string;
  ref: string;
  version: number;
  detail_level: string;
  payload: EstimatePayload;
  status: string;
  ai_job_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface EstimatePdfResult {
  document_id: string;
  filename: string;
  size_bytes: number;
  download_url: string;
}

export function listEstimates(projectId: string): Promise<EstimateRead[]> {
  return api(`/estimates?project_id=${encodeURIComponent(projectId)}`);
}

export function getEstimate(id: string): Promise<EstimateRead> {
  return api(`/estimates/${id}`);
}

export function updateEstimate(
  id: string,
  payload: EstimatePayload,
  status?: string,
): Promise<EstimateRead> {
  return api(`/estimates/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ payload, status: status ?? null }),
  });
}

export function generateEstimatePdf(id: string): Promise<EstimatePdfResult> {
  return api(`/estimates/${id}/pdf`, { method: "POST" });
}

// --- shared totals math (mirrors backend estimate_pdf.py) ------------------

export function estimateTotals(payload: EstimatePayload) {
  const overhead = Number(payload.overhead) || 0;
  const profit = Number(payload.profit) || 0;
  const margin = (1 + overhead / 100) * (1 + profit / 100);
  const subtotal = (payload.categories ?? []).reduce(
    (s, c) => s + (Number(c.total) || 0) * margin,
    0,
  );
  const allowances = Number(payload.allowances) || 0;
  const taxable = subtotal + allowances;
  const gst = taxable * 0.05;
  return { margin, subtotal, allowances, gst, total: taxable + gst };
}

export function money(n: number): string {
  return n.toLocaleString("en-CA", { style: "currency", currency: "CAD" });
}
