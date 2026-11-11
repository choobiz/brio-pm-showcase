import { apiError } from "./apiError";
// Through the same-origin proxy, like every other lib. Until 2026-10-15 this
// called the API host directly with `credentials: "include"`; the session
// cookie never reached it, so the Clients page showed "API 401: not
// authenticated" to everyone, superadmin included.
const BASE = "/api/backend";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) throw await apiError(res);
  return res.json() as Promise<T>;
}

export interface ClientListItem {
  id: string;
  name: string;
  primary_email: string | null;
  primary_phone: string | null;
  project_count: number;
  last_activity: string | null;
  tags: string[];
}

export interface ClientProjectSummary {
  id: string;
  slug: string;
  name: string;
  address: string | null;
  status: string;
  contract_value: string;
  total_billed: string;
  total_paid: string;
}

export interface ClientDocumentSummary {
  id: string;
  project_id: string;
  category: string;
  filename: string;
  created_at: string | null;
}

export interface ClientInvoiceSummary {
  id: string;
  project_id: string;
  number: string;
  type: string;
  status: string;
  total: string;
  paid_amount: string;
}

export interface ClientPaymentSummary {
  id: string;
  invoice_id: string;
  method: string;
  amount: string;
  paid_at: string | null;
}

export interface ClientDetail {
  id: string;
  name: string;
  normalized_name: string;
  email: string | null;
  phone: string | null;
  address: string | null;
  emails: string[];
  phones: string[];
  addresses: string[];
  tags: string[];
  notes: string | null;
  first_seen: string | null;
  last_seen: string | null;
  created_at: string;
  updated_at: string;
  projects: ClientProjectSummary[];
  documents: ClientDocumentSummary[];
  invoices: ClientInvoiceSummary[];
  payments: ClientPaymentSummary[];
  total_billed: string;
  total_paid: string;
  total_outstanding: string;
  last_activity: string | null;
}

export interface FindOrCreateBody {
  name: string;
  email?: string;
  phone?: string;
  address?: string;
}

export interface FindOrCreateResponse {
  client: ClientDetail;
  created: boolean;
}

export function listClients(q?: string): Promise<ClientListItem[]> {
  const qs = q && q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "";
  return api(`/clients${qs}`);
}

export function getClient(id: string): Promise<ClientDetail> {
  return api(`/clients/${id}`);
}

export function findOrCreateClient(body: FindOrCreateBody): Promise<FindOrCreateResponse> {
  return api(`/clients/find-or-create`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function formatMoney(amount: string | number | null | undefined): string {
  if (amount === null || amount === undefined) return "—";
  const n = typeof amount === "string" ? parseFloat(amount) : amount;
  if (Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 }).format(n);
}
