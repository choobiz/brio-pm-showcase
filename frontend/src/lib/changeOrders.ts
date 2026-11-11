// Change orders — office side (manager/superadmin), via the /api/backend proxy.

const BASE = "/api/backend";

export interface ChangeOrder {
  id: string;
  number: number;
  reference: string;
  title: string;
  description: string | null;
  cost_impact: number; // pre-tax
  cost_impact_incl_gst: number;
  schedule_impact_days: number;
  status: "draft" | "pending" | "approved" | "rejected" | "void";
  decided_at: string | null;
  owner_note: string | null;
  created_at: string | null;
  /** Pricing breakdown behind cost_impact. Empty for hand-written orders. */
  detail?: {
    line_items?: { description?: string; qty?: number; unit?: string; unit_cost?: number; amount?: number }[];
    assumptions?: string[];
    exclusions?: string[];
    generated?: boolean;
    instruction?: string | null;
  };
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.status === 204 ? (undefined as T) : (r.json() as Promise<T>);
}

const base = (pid: string) => `/projects/${pid}/change-orders`;

export const listChangeOrders = (pid: string) => api<ChangeOrder[]>(base(pid));

export const createChangeOrder = (
  pid: string,
  body: { title: string; description?: string; cost_impact: number; schedule_impact_days: number },
) => api<ChangeOrder>(base(pid), { method: "POST", body: JSON.stringify(body) });

export const updateChangeOrder = (
  pid: string,
  coId: string,
  body: Partial<{
    title: string;
    description: string;
    cost_impact: number;
    schedule_impact_days: number;
    detail: ChangeOrder["detail"];
  }>,
) => api<ChangeOrder>(`${base(pid)}/${coId}`, { method: "PATCH", body: JSON.stringify(body) });

export const submitChangeOrder = (pid: string, coId: string) =>
  api<ChangeOrder>(`${base(pid)}/${coId}/submit`, { method: "POST" });

export const voidChangeOrder = (pid: string, coId: string) =>
  api<ChangeOrder>(`${base(pid)}/${coId}/void`, { method: "POST" });

export interface ChangeOrderPdfResult {
  document_id: string;
  filename: string;
  size_bytes: number;
  download_url: string;
}

/** Render the change order to a branded PDF and return a download URL. */
export const changeOrderPdf = (pid: string, coId: string) =>
  api<ChangeOrderPdfResult>(`${base(pid)}/${coId}/pdf`, { method: "POST" });
