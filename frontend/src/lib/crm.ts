import { apiError } from "./apiError";
// Goes through the /api/backend proxy, NOT straight at the API.
//
// A direct browser fetch cannot authenticate here: the frontend is served from
// brio-pm.example.com and the API from 104-198-45-146.nip.io, so the
// NextAuth session cookie — scoped to the frontend origin — is never sent, and
// credentials:"include" attaches nothing. Every request came back 401.
//
// The proxy is a Next route handler, so it reads the cookie server-side and
// forwards it upstream. Every other lib in this app already does it this way;
// lib/clients.ts is the lone exception and almost certainly has the same latent
// bug wherever it is called from the browser.
const PROXY = "/api/backend";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${PROXY}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) throw await apiError(res);
  return res.json() as Promise<T>;
}

export interface CrmContact {
  id: string;
  name: string;
  kind: string | null;
  segment: string | null;
  tier: string | null;
  email: string | null;
  phone: string | null;
  website: string | null;
  contact_confidence: string | null;
  /** Up to three of the firm's actual recent permits, newest first. */
  recent_projects:
    | {
        address: string;
        value: number | null;
        work: string | null;
        issued: string | null;
        permit: string | null;
      }[]
    | null;
  permits_total: number | null;
  value_as_applicant: number | null;
  cities: string | null;
  top_use_category: string | null;
  bc_registration: string | null;
  entity_type: string | null;
  registry_status: string | null;
  excluded_reason: string | null;
}

export interface CrmContactDetail extends CrmContact {
  legal_name: string | null;
  permit_name: string | null;
  permits_as_applicant: number | null;
  address: string | null;
  notes: string | null;
  tags: string[];
  opportunities: CrmOpportunity[];
  interactions: CrmInteraction[];
}

export interface CrmOpportunity {
  id: string;
  client_id: string;
  track: string;
  status: string;
  next_action: string | null;
  next_action_at: string | null;
  owner_id: string | null;
  notes: string | null;
  last_contacted_at: string | null;
}

export interface CrmInteraction {
  id: string;
  channel: string;
  direction: string;
  occurred_at: string;
  summary: string | null;
  outcome: string | null;
  created_by_id: string | null;
}

export type QueueReason = "overdue" | "due_today" | "no_action" | "upcoming";

export type Channel = "call" | "email" | "website" | "find_contact";

export interface QueueItem {
  opportunity: CrmOpportunity;
  contact: CrmContact;
  owner_name: string | null;
  last_interaction: CrmInteraction | null;
  interaction_count: number;
  reason: QueueReason;
  days_overdue: number;
  channel: Channel;
  channel_target: string | null;
  channel_hint: string | null;
}

export interface QueueResponse {
  items: QueueItem[];
  counts: Record<string, number>;
}

export function getQueue(opts: {
  track?: string; owner?: string; horizonDays?: number;
} = {}) {
  const p = new URLSearchParams();
  if (opts.track) p.set("track", opts.track);
  if (opts.owner) p.set("owner", opts.owner);
  if (opts.horizonDays != null) p.set("horizon_days", String(opts.horizonDays));
  return api<QueueResponse>(`/crm/queue?${p}`);
}

export function listCrmContacts(opts: {
  tier?: string; segment?: string; confidence?: string;
  hasContact?: boolean; city?: string; q?: string;
  limit?: number; offset?: number;
} = {}) {
  const p = new URLSearchParams();
  if (opts.tier) p.set("tier", opts.tier);
  if (opts.segment) p.set("segment", opts.segment);
  if (opts.confidence) p.set("confidence", opts.confidence);
  if (opts.hasContact != null) p.set("has_contact", String(opts.hasContact));
  if (opts.city) p.set("city", opts.city);
  if (opts.q) p.set("q", opts.q);
  p.set("limit", String(opts.limit ?? 100));
  if (opts.offset) p.set("offset", String(opts.offset));
  return api<CrmContact[]>(`/crm/contacts?${p}`);
}

export function getCrmContact(id: string) {
  return api<CrmContactDetail>(`/crm/contacts/${id}`);
}

export function logInteraction(oppId: string, body: {
  channel: string; direction?: string; summary?: string; outcome?: string;
  next_action?: string; next_action_at?: string; status?: string;
}) {
  return api<CrmOpportunity>(`/crm/opportunities/${oppId}/log`, {
    method: "POST", body: JSON.stringify(body),
  });
}

export function promoteContact(clientId: string, track = "construction") {
  return api<CrmOpportunity>(`/crm/contacts/${clientId}/opportunities`, {
    method: "POST", body: JSON.stringify({ track }),
  });
}

// ── Card leads ─────────────────────────────────────────────────────────────
// People who sent their details from a BRIO digital business card
// (builtbybrio.com/card/<slug>). Not contacts until Amir says so.

export type CardLeadStatus = "new" | "contacted" | "archived";
export type CardLeadRole = "developer" | "owner" | "builder" | "trade" | "other";

export interface CardLead {
  id: string;
  created_at: string;
  name: string;
  company: string | null;
  role: CardLeadRole | null;
  phone: string | null;
  email: string | null;
  note: string | null;
  card_slug: string;
  source: string | null;
  status: CardLeadStatus;
  contacted_at: string | null;
}

export function listCardLeads(status?: CardLeadStatus) {
  const p = status ? `?status=${status}` : "";
  return api<CardLead[]>(`/crm/card-leads${p}`);
}

export function setCardLeadStatus(id: string, status: CardLeadStatus) {
  return api<CardLead>(`/crm/card-leads/${id}`, {
    method: "PATCH", body: JSON.stringify({ status }),
  });
}

export function money(n: number | null): string {
  if (!n) return "—";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}k`;
  return `$${n}`;
}
