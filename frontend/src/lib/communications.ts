// Project communications (Conversations) — office side, via the /api/backend proxy.

const BASE = "/api/backend";

export type CommCategory =
  | "general" | "rfi" | "selection" | "quote" | "change_order" | "schedule"
  | "financial" | "deficiency" | "permit_inspection" | "meeting" | "warranty" | "other";

export const CATEGORY_LABELS: Record<CommCategory, string> = {
  general: "General", rfi: "RFI / Clarification", selection: "Selection",
  quote: "Quote / Bid", change_order: "Change order", schedule: "Schedule",
  financial: "Financial", deficiency: "Deficiency", permit_inspection: "Permit / Inspection",
  meeting: "Meeting", warranty: "Warranty", other: "Other",
};

export const CATEGORY_CLS: Record<CommCategory, string> = {
  general: "bg-slate-100 text-slate-600", rfi: "bg-sky-100 text-sky-800",
  selection: "bg-blue-100 text-blue-800", quote: "bg-emerald-100 text-emerald-800",
  change_order: "bg-amber-100 text-amber-800", schedule: "bg-indigo-100 text-indigo-800",
  financial: "bg-violet-100 text-violet-800", deficiency: "bg-red-100 text-red-800",
  permit_inspection: "bg-teal-100 text-teal-800", meeting: "bg-fuchsia-100 text-fuchsia-800",
  warranty: "bg-lime-100 text-lime-800", other: "bg-slate-100 text-slate-500",
};

export interface Communication {
  id: string;
  source: string;
  direction: string | null;
  channel: string | null;
  from_name: string | null;
  from_email: string | null;
  to_emails: string | null;
  subject: string | null;
  body: string;
  occurred_at: string | null;
  category: CommCategory | null;
  summary: string | null;
  action_items: string[];
  urgency: "low" | "normal" | "high" | null;
  has_quote: boolean;
  processed: boolean;
  created_at: string | null;
}

export interface CommHighlights {
  recent: Communication[];
  open_action_items: { item: string; communication_id: string; category: string | null; occurred_at: string | null }[];
  urgent: Communication[];
  category_counts: Record<string, number>;
}

export interface CommInput {
  body: string;
  subject?: string;
  from_name?: string;
  from_email?: string;
  to_emails?: string;
  channel?: string;
  direction?: string;
  occurred_at?: string;
  classify?: boolean;
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

const base = (pid: string) => `/projects/${pid}/communications`;

export const listCommunications = (pid: string, category?: string) =>
  api<Communication[]>(`${base(pid)}${category ? `?category=${category}` : ""}`);

export const getHighlights = (pid: string) => api<CommHighlights>(`${base(pid)}/highlights`);

export const createCommunication = (pid: string, body: CommInput) =>
  api<Communication>(base(pid), { method: "POST", body: JSON.stringify(body) });

export const reclassifyCommunication = (pid: string, cid: string) =>
  api<Communication>(`${base(pid)}/${cid}/reclassify`, { method: "POST" });

export const deleteCommunication = (pid: string, cid: string) =>
  api<void>(`${base(pid)}/${cid}`, { method: "DELETE" });

export interface InboundAddress {
  address: string;
  token: string;
  domain: string;
}

export const getInboundAddress = (pid: string) =>
  api<InboundAddress>(`${base(pid)}/inbound-address`);
