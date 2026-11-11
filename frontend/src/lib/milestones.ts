import { apiError } from "./apiError";
// Milestone bookings and letters (plan approved 2026-10-16). Client API via the
// /api/backend proxy so calls carry the session.

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

export type MilestoneStatus = "to_book" | "requested" | "booked" | "received" | "done" | "not_needed";
export const STATUS_LABEL: Record<MilestoneStatus, string> = {
  to_book: "To book",
  requested: "Requested",
  booked: "Booked",
  received: "Documents in",
  done: "Done",
  not_needed: "Not needed",
};

export interface RequiredDoc {
  name: string;
  status: "missing" | "received" | "not_needed";
  document_id?: string;
  note?: string;
}

export interface ThreadEntry {
  at: string;
  direction: "out" | "in";
  subject?: string;
  to?: string;
  from?: string;
  email_id?: string;
  communication_id?: string;
  document_ids: string[];
}

export interface Milestone {
  id: string;
  ref: string;
  template_key: string | null;
  stage: string;
  stage_order: number;
  title: string;
  party_role: string;
  email_kind: "booking" | "document_request" | "submittal" | "notice";
  status: MilestoneStatus;
  lead_days: number | null;
  lead_note: string | null;
  activity_code: string | null;
  activity_name: string | null;
  need_by: string | null;
  book_by: string | null;
  late: boolean;
  required_docs: RequiredDoc[];
  docs_ready: number;
  why: string | null;
  source: string | null;
  booked_for: string | null;
  notes: string | null;
  confirmed: boolean;
  thread: ThreadEntry[];
  emails: { id: string; status: "drafting" | "draft" | "sent" | "failed"; subject: string; updated_at: string | null }[];
  party: { name: string | null; company: string | null; email: string | null; phone: string | null } | null;
}

export interface TeamMember {
  id: string;
  role: string;
  name: string | null;
  company: string | null;
  email: string | null;
  phone: string | null;
  notes: string | null;
}

export interface MilestoneList {
  items: Milestone[];
  team: TeamMember[];
  roles?: string[];
  facts?: Record<string, { value: unknown; source?: string; quote?: string; firm?: string }>;
  added?: number;
}

export interface MilestoneEmail {
  id: string;
  milestone_id: string;
  ref: string | null;
  to_email: string | null;
  to_name: string | null;
  cc: string | null;
  subject: string;
  body: string;
  subject_as_sent: string | null;
  from_as_sent: string | null;
  project_address: string;
  attachment_ids: string[];
  status: "drafting" | "draft" | "sent" | "failed";
  error: string | null;
  sent_at: string | null;
  sent_by: string | null;
  sent_envelope: Record<string, unknown> | null;
}

const p = (pid: string) => `/projects/${encodeURIComponent(pid)}`;

export const listMilestones = (pid: string) => api<MilestoneList>(`${p(pid)}/milestones`);
export const buildMilestones = (pid: string) =>
  api<MilestoneList>(`${p(pid)}/milestones/build`, { method: "POST", body: "{}" });
export const patchMilestone = (pid: string, mid: string, body: Partial<Pick<Milestone, "status" | "booked_for" | "notes" | "confirmed" | "required_docs" | "lead_days" | "activity_code">>) =>
  api<MilestoneList>(`${p(pid)}/milestones/${mid}`, { method: "PATCH", body: JSON.stringify(body) });
export const putTeam = (pid: string, role: string, body: Partial<Omit<TeamMember, "id" | "role">>) =>
  api<MilestoneList>(`${p(pid)}/team/${role}`, { method: "PUT", body: JSON.stringify(body) });
export const requestDraft = (pid: string, mid: string, body: { attachment_ids: string[]; instructions?: string }) =>
  api<{ email_id: string; job_id: string }>(`${p(pid)}/milestones/${mid}/draft`, { method: "POST", body: JSON.stringify(body) });
export const getMilestoneEmail = (pid: string, eid: string) => api<MilestoneEmail>(`${p(pid)}/milestone-emails/${eid}`);
export const saveMilestoneEmail = (pid: string, eid: string, body: Partial<Pick<MilestoneEmail, "to_email" | "to_name" | "cc" | "subject" | "body" | "attachment_ids">>) =>
  api<MilestoneEmail>(`${p(pid)}/milestone-emails/${eid}`, { method: "PUT", body: JSON.stringify(body) });
/** Sends only with confirm=true, which the confirmation dialog passes after the PM has read the exact email. */
export const sendMilestoneEmail = (pid: string, eid: string) =>
  api<MilestoneEmail>(`${p(pid)}/milestone-emails/${eid}/send`, { method: "POST", body: JSON.stringify({ confirm: true }) });

export const ROLE_LABEL: Record<string, string> = {
  municipality: "Municipality (inspections)",
  structural_engineer: "Structural engineer",
  geotechnical_engineer: "Geotechnical engineer",
  surveyor: "Land surveyor (BCLS)",
  stormwater_engineer: "Stormwater engineer",
  envelope_professional: "Building envelope professional",
  energy_advisor: "Energy advisor",
  arborist: "Arborist",
  electrician: "Electrician",
  plumber: "Plumber",
  concrete_supplier: "Concrete supplier",
  truss_supplier: "Truss / joist supplier",
  contractor: "BRIO (us)",
  other: "Other",
};
