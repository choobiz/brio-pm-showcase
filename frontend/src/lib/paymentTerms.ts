// Payment-terms templates + per-project payment schedule — via the /api/backend proxy.

const BASE = "/api/backend";

export interface TermEntry {
  milestone: string;
  pct: number;
  detail?: string;
}

export interface Template {
  id: string;
  name: string;
  description?: string | null;
  is_builtin: boolean;
  entries: TermEntry[];
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!r.ok) {
    let detail = `API ${r.status}`;
    try { detail = (await r.json())?.detail ?? detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return r.status === 204 ? (undefined as T) : (r.json() as Promise<T>);
}

export const listTemplates = () => api<Template[]>(`/payment-terms/templates`);

export const saveTemplate = (body: { name: string; description?: string; entries: TermEntry[] }) =>
  api<Template>(`/payment-terms/templates`, { method: "POST", body: JSON.stringify(body) });

export const deleteTemplate = (id: string) =>
  api<void>(`/payment-terms/templates/${id}`, { method: "DELETE" });

export const getProjectTerms = (pid: string) =>
  api<{ has_contract: boolean; entries: TermEntry[] }>(`/projects/${pid}/payment-terms`);

export const setProjectTerms = (pid: string, entries: TermEntry[]) =>
  api<{ ok: boolean; sum: number; entries: TermEntry[] }>(`/projects/${pid}/payment-terms`, {
    method: "PUT",
    body: JSON.stringify({ entries }),
  });
