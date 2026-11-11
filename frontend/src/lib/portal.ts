// Owner (client) portal — client-side API via the /api/backend proxy.

const BASE = "/api/backend";

export interface PortalProject {
  id: string;
  name: string;
  address: string | null;
  status: string;
  type: string | null;
}

export interface Overview {
  id: string;
  name: string;
  address: string | null;
  status: string;
  type: string | null;
  current_phase: string | null;
  next_milestone: { label: string; date: string } | null;
  target_finish: string | null;
  last_update: string | null;
  hero_photo: { url: string; caption: string } | null;
}

export interface MilestoneItem {
  label: string;
  date: string;
  state: "done" | "current" | "upcoming";
}

export interface Photo {
  url: string;
  caption: string;
  date: string | null;
}

export interface PortalDoc {
  name: string;
  category: string;
  url: string;
  date: string | null;
}

async function api<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!r.ok) {
    const e = new Error(`API ${r.status}`) as Error & { status: number };
    e.status = r.status;
    throw e;
  }
  return r.json() as Promise<T>;
}

export const listMyProjects = () => api<PortalProject[]>("/portal/projects");
export const getOverview = (id: string) => api<Overview>(`/portal/projects/${id}/overview`);
export const getMilestones = (id: string) =>
  api<{ target_finish: string | null; items: MilestoneItem[] }>(`/portal/projects/${id}/milestones`);
export const getPhotos = (id: string) => api<Photo[]>(`/portal/projects/${id}/photos`);
export const getDocuments = (id: string) => api<PortalDoc[]>(`/portal/projects/${id}/documents`);
export const packageUrl = (id: string) => `${BASE}/portal/projects/${id}/package.pdf`;

export interface Draw {
  milestone: string;
  pct: number;
  amount: number;
  status: "paid" | "partial" | "due";
}
export interface Payments {
  contract_total: number;
  change_orders_total: number;
  revised_total: number;
  paid_to_date: number;
  remaining: number;
  draws: Draw[];
}
export const getPayments = (id: string) => api<Payments>(`/portal/projects/${id}/payments`);

export interface PortalChangeOrder {
  id: string;
  reference: string;
  title: string;
  description: string | null;
  cost_impact_incl_gst: number;
  schedule_impact_days: number;
  status: "pending" | "approved" | "rejected";
  owner_note: string | null;
}
export const getChangeOrders = (id: string) =>
  api<PortalChangeOrder[]>(`/portal/projects/${id}/change-orders`);

export async function decideChangeOrder(
  coId: string,
  decision: "approve" | "reject",
  note?: string,
): Promise<PortalChangeOrder> {
  const r = await fetch(`${BASE}/portal/change-orders/${coId}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, note }),
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json();
}

export interface PortalSelectionOption {
  id: string;
  label: string;
  description: string | null;
  price_delta: number | null;
}
export interface PortalSelection {
  id: string;
  title: string;
  category: string | null;
  description: string | null;
  decision_type: "select" | "approve";
  options: PortalSelectionOption[];
  status: "pending" | "submitted" | "confirmed";
  selected_option_id: string | null;
  selected_option: PortalSelectionOption | null;
  client_comment: string | null;
  due_date?: string | null;
  /** When the work needs the decision (start of that work minus the supplier lead time). */
  decide_by?: string | null;
}
export const getSelections = (id: string) =>
  api<PortalSelection[]>(`/portal/projects/${id}/selections`);

export async function decideSelection(
  projectId: string,
  sid: string,
  body: { option_id?: string; decision?: "approved" | "changes_requested"; comment?: string },
): Promise<PortalSelection> {
  const r = await fetch(`${BASE}/portal/projects/${projectId}/selections/${sid}/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json();
}

export interface PortalMessage {
  id: string;
  sender_role: "owner" | "office";
  body: string;
  read_at: string | null;
  created_at: string | null;
}
export const getMessages = (id: string) =>
  api<{ messages: PortalMessage[] }>(`/portal/projects/${id}/messages`);

export async function sendMessage(projectId: string, body: string): Promise<PortalMessage> {
  const r = await fetch(`${BASE}/portal/projects/${projectId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json();
}

export interface PortalInvoice {
  id: string;
  number: string;
  type: string;
  date: string | null;
  description: string | null;
  total: number;
  paid: number;
  balance: number;
  status: "sent" | "partial" | "paid" | "overdue";
}
export const getInvoices = (id: string) =>
  api<PortalInvoice[]>(`/portal/projects/${id}/invoices`);
export const getInvoicePdf = (projectId: string, invoiceId: string) =>
  api<{ download_url: string }>(`/portal/projects/${projectId}/invoices/${invoiceId}/pdf`);

export interface Warranty {
  workmanship_years: number;
  home_warranty_required: boolean;
  home_warranty_provider: string | null;
  projected_completion: string | null;
}
export const getWarranty = (id: string) =>
  api<Warranty>(`/portal/projects/${id}/warranty`);

// Proposal (round 2, 2026-11-02): the proposal sent to the portal, and accepting it.
export interface PortalProposal {
  id: string;
  version_number: number;
  headline: string | null;
  subtotal: number | null;
  gst: number | null;
  total: number | null;
  valid_until: string | null;
  expired: boolean;
  pdf_path: string;
  base: number;
  options: { name: string; amount: number }[];
  accepted: { typed_name: string; accepted_at: string; total: number | null; options?: { name: string; amount: number }[] } | null;
}

export const getProposal = (id: string) =>
  api<{ proposal: PortalProposal | null }>(`/portal/projects/${id}/proposal`);

export const proposalPdfUrl = (p: PortalProposal) => `${BASE}${p.pdf_path}`;

/** Accept: the server's reason is shown as is (e.g. the price has expired). */
export async function acceptProposal(id: string, prid: string, typedName: string, options?: string[]): Promise<PortalProposal> {
  const r = await fetch(`${BASE}/portal/projects/${id}/proposal/${prid}/accept`, {
    method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ typed_name: typedName, agree: true, ...(options ? { options } : {}) }),
  });
  if (!r.ok) {
    let msg = "Something went wrong. Please try again, or message us.";
    try { const j = await r.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* keep default */ }
    throw new Error(msg);
  }
  return (await r.json()).proposal as PortalProposal;
}
