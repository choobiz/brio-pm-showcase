import { apiError } from "./apiError";
// Analytics — portfolio cost intelligence (L1).

const BASE = "/api/backend";

async function api<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw await apiError(res);
  return res.json() as Promise<T>;
}

export interface CostAnalytics {
  summary: {
    total_amount: number;
    cost_count: number;
    projects_with_costs: number;
    category_count: number;
  };
  by_category: { category: string; total: number; count: number; avg: number }[];
  by_project: {
    project_id: string;
    slug: string;
    name: string;
    total: number;
    count: number;
  }[];
  by_year: { year: number | null; total: number; count: number }[];
  by_kind: { kind: string; total: number; count: number }[];
}

export function getCostAnalytics(): Promise<CostAnalytics> {
  return api("/analytics/costs");
}

export interface VarianceProject {
  project_id: string;
  slug: string;
  name: string;
  estimate: { ref: string; subtotal: number; version: number };
  actual: { total: number; count: number };
  delta: number;
  pct: number | null;
}

export interface VarianceAnalytics {
  projects: VarianceProject[];
}

export function getVarianceAnalytics(): Promise<VarianceAnalytics> {
  return api("/analytics/variance");
}
