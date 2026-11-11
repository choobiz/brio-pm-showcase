import { apiError } from "./apiError";
// Track E — invoices: create / list / edit / payments / PDF / send.

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

export type InvoiceType = "progress" | "tm" | "change_order" | "retainer";
export type InvoiceStatus =
  | "draft"
  | "sent"
  | "paid"
  | "partial"
  | "overdue"
  | "void";

export const INVOICE_TYPES: { value: InvoiceType; label: string }[] = [
  { value: "progress", label: "Progress — milestone billing" },
  { value: "tm", label: "Time & Materials — actual hours + materials" },
  { value: "change_order", label: "Change Order — work beyond scope" },
  { value: "retainer", label: "Retainer — upfront deposit" },
];

export interface InvoicePayload {
  date?: string;
  dueDate?: string;
  terms?: string;
  description?: string;
  notes?: string;
  milestone?: string;
  changeOrderNumber?: string;
  reason?: string;
  retainerTerms?: string;
  estimateRef?: string;
  sourceEstimate?: { versionId?: string; versionNumber?: number; reference?: string };
  sourceChangeOrder?: { changeOrderId?: string; number?: number; reference?: string };
  materialMarkup?: number;
  lineItems?: {
    description?: string;
    percentage?: number;
    amount?: number;
    labor?: number;
    materials?: number;
  }[];
  labor?: { worker?: string; description?: string; hours?: number; rate?: number }[];
  materials?: { item?: string; supplier?: string; qty?: number; unitCost?: number }[];
  /** Added AFTER GST and never taxed: a GST catch-up from an earlier invoice,
   *  a credit, a holdback deduction. Does not move the subtotal. Negative
   *  amounts are valid and are how a credit is expressed. */
  adjustments?: { description?: string; amount?: number }[];
  adjustmentsTotal?: number;
  subtotal?: number;
  gst?: number;
  total?: number;
  amount?: number;
  [key: string]: unknown;
}

export interface InvoiceRead {
  id: string;
  project_id: string;
  number: string;
  type: InvoiceType;
  payload: InvoicePayload;
  status: InvoiceStatus;
  paid_amount: string;
  paid_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PaymentRead {
  id: string;
  invoice_id: string;
  amount: string;
  method: string;
  paid_at: string;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export function listInvoices(projectId: string): Promise<InvoiceRead[]> {
  return api(`/invoices?project_id=${encodeURIComponent(projectId)}`);
}

/** Delete a DRAFT invoice. The API refuses anything already issued (409).
 *  Separate from api<T> because a 204 has no body to parse. */
export async function deleteInvoice(id: string): Promise<void> {
  const res = await fetch(`${BASE}/invoices/${id}`, { method: "DELETE", cache: "no-store" });
  if (!res.ok) throw await apiError(res);
}

export function getInvoice(id: string): Promise<InvoiceRead> {
  return api(`/invoices/${id}`);
}

export interface InvoiceSeed {
  payload: InvoicePayload;
  estimate_reference: string | null;
  estimate_version_number: number | null;
  estimate_subtotal: number;
  matches_estimate_subtotal: boolean;
}

/** Propose an invoice from the project's ACTIVE estimate version. Reads only —
 *  the numbers are money, so a person reviews them before creating. */
export function seedInvoiceFromEstimate(
  projectId: string,
  type: InvoiceType,
): Promise<InvoiceSeed> {
  return api(
    `/invoices/seed-from-estimate?project_id=${encodeURIComponent(projectId)}&type=${type}`,
  );
}

export interface ChangeOrderInvoiceSeed {
  payload: InvoicePayload;
  change_order_reference: string | null;
  change_order_status: string | null;
  change_order_subtotal: number;
  matches_change_order: boolean;
  warnings: string[];
}

/** Propose a change-order invoice from a ChangeOrder's own breakdown. Reads
 *  only. A change order bills work that is NOT in the estimate, so it cannot
 *  be seeded from the active estimate version. */
export function seedInvoiceFromChangeOrder(
  changeOrderId: string,
): Promise<ChangeOrderInvoiceSeed> {
  return api(
    `/invoices/seed-from-change-order?change_order_id=${encodeURIComponent(changeOrderId)}`,
  );
}

/** A key for one create attempt, reused across retries of that attempt.
 *
 * Sent in the BODY, not an Idempotency-Key header: the proxy at
 * api/backend/[...path] forwards an allow-list of headers (Content-Type,
 * Accept, Cookie) and silently drops the rest, so a header would arrive as
 * None and the protection would be missing exactly where it looked present. */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto;
  if (c?.randomUUID) return c.randomUUID();
  // randomUUID needs a secure context. Falling back rather than throwing, since
  // a missing key only loses the protection; it does not break the create.
  return `k-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

export function createInvoice(body: {
  project_id: string;
  type: InvoiceType;
  payload: InvoicePayload;
  idempotency_key?: string;
}): Promise<InvoiceRead> {
  return api("/invoices", { method: "POST", body: JSON.stringify(body) });
}

export function updateInvoice(
  id: string,
  payload?: InvoicePayload,
  status?: InvoiceStatus,
): Promise<InvoiceRead> {
  // payload is omitted rather than sent as null when only the status changes:
  // the server replaces the whole payload when it receives one.
  const body: Record<string, unknown> = {};
  if (payload !== undefined) body.payload = payload;
  if (status !== undefined) body.status = status;
  return api(`/invoices/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function listPayments(id: string): Promise<PaymentRead[]> {
  return api(`/invoices/${id}/payments`);
}

export function recordPayment(
  id: string,
  body: { amount: number; method: string; notes?: string },
): Promise<InvoiceRead> {
  return api(`/invoices/${id}/payments`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function generateInvoicePdf(
  id: string,
): Promise<{ document_id: string; filename: string; download_url: string }> {
  return api(`/invoices/${id}/pdf`, { method: "POST" });
}

export function sendInvoice(
  id: string,
): Promise<{ sent_to: string; document_id: string; status: string }> {
  return api(`/invoices/${id}/send`, { method: "POST" });
}

// Signed token for the public, no-auth client invoice-view link.
export function getShareToken(id: string): Promise<{ token: string }> {
  return api(`/invoices/${id}/share-link`);
}

export function money(n: number | string): string {
  return Number(n).toLocaleString("en-CA", {
    style: "currency",
    currency: "CAD",
  });
}
