import { apiError } from "./apiError";
// Submittal review (wireframes approved by Amir 2026-10-17). Client API via the
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

export type SubmittalStatus =
  | "draft"
  | "reviewing"
  | "review_failed"
  | "needs_call"
  | "waiting"
  | "closed"
  | "superseded";
export const STATUS_LABEL: Record<SubmittalStatus, string> = {
  draft: "Not run",
  reviewing: "Reviewing…",
  review_failed: "Review didn't finish",
  needs_call: "Needs your call",
  waiting: "Waiting on others",
  closed: "Closed",
  superseded: "Replaced by a newer revision",
};

export type Outcome = "no_exceptions" | "as_noted" | "revise_resubmit" | "rejected";
export const OUTCOMES: { value: Outcome; label: string }[] = [
  { value: "no_exceptions", label: "Reviewed, no exceptions" },
  { value: "as_noted", label: "Reviewed as noted" },
  { value: "revise_resubmit", label: "Revise and resubmit" },
  { value: "rejected", label: "Rejected" },
];

export type Result = "critical" | "confirm" | "match" | "not_shown";
export const RESULT_LABEL: Record<Result, string> = {
  critical: "Critical",
  confirm: "Confirm",
  match: "Match",
  not_shown: "Not shown",
};
export type Call = "ask" | "fine" | "answered";
export type Party = "engineer" | "supplier";

export interface Counts {
  critical: number;
  confirm: number;
  match: number;
  not_shown: number;
}

export interface SubmittalRow {
  id: string;
  ref: string;
  name: string;
  trade: string;
  trade_label: string;
  supplier: string | null;
  revision: string;
  status: SubmittalStatus;
  counts: Counts;
  checked_against: string[];
  outcome: Outcome | null;
  outcome_label: string | null;
  milestone: { ref: string; title: string } | null;
  created_at: string | null;
  reviewed_at: string | null;
  cleared: boolean;
  error: string | null;
}

export interface Finding {
  id: string;
  check_key: string;
  check: string;
  shop: string;
  shop_ref: string;
  plan: string;
  plan_ref: string;
  result: Result;
  why: string;
  ask_who: Party | "none";
  question: string;
  call: Call | null;
  call_note: string | null;
  previous: "fixed" | "still_open" | "new" | null;
}

export interface DocRef {
  id: string;
  filename: string;
  mime: string | null;
  pages: number;
}

export interface Gate {
  signed_received: boolean;
  no_open_critical: boolean;
  outcome_ok: boolean;
  cleared: boolean;
}

export interface SubmittalEmail {
  party: Party;
  to_name: string;
  to_email: string;
  subject: string;
  body: string;
  finding_ids: string[];
  copied_at: string | null;
  subject_as_sent?: string;
  project_address?: string;
}

export interface SubmittalDetail extends SubmittalRow {
  summary: string | null;
  findings: Finding[];
  quote: {
    supplier?: string;
    number?: string;
    date?: string;
    subtotal?: number | null;
    tax?: number | null;
    total?: number | null;
    bom_matches?: boolean | null;
    notes?: string[];
    lines?: QuoteLine[];
  };
  submittal_docs: DocRef[];
  governing_docs: DocRef[];
  instructions: string | null;
  cost_usd: number | null;
  approved_usd: number | null;
  outcome_note: string | null;
  outcome_by: string | null;
  outcome_at: string | null;
  signed_received: boolean;
  gate: Gate;
  questions: Partial<Record<Party, string[]>>;
  parties: {
    engineer: { role: string; name?: string; company?: string; email?: string };
    supplier: { role: string; company?: string | null };
  };
  emails: SubmittalEmail[];
  thread: { at: string; direction: "in" | "out"; from?: string; subject?: string; document_ids?: string[] }[];
  history: { at: string; by: string; text: string }[];
  revisions: { id: string; revision: string; status: SubmittalStatus }[];
  milestone_id: string | null;
  project_address: string;
}

export interface QuoteLine {
  i: number;
  description: string;
  sku: string | null;
  qty: number | null;
  unit: string | null;
  unit_price: number | null;
  amount: number | null;
  material_id: string | null;
}

export interface Checklist {
  key: string;
  label: string;
  engineer_role: string;
  checks: { key: string; label: string; what: string }[];
  governing: string[];
  edited: boolean;
  builtin: boolean;
}

export interface Citation {
  document: DocRef;
  ref: string;
  kind: "pdf" | "image";
  page: number | null;
  pages?: number;
  found: string[];
  searched: string[];
  image: string | null;
  media_type: string;
  note: string | null;
}

export interface Cost {
  usd: number;
  how: string;
  pages: number;
}

const P = (pid: string) => `/projects/${pid}/submittals`;

export const listSubmittals = (pid: string) => api<SubmittalRow[]>(P(pid));
export const listChecklists = (pid: string) => api<Checklist[]>(`${P(pid)}/checklists`);
export const getSubmittal = (pid: string, sid: string) => api<SubmittalDetail>(`${P(pid)}/${sid}`);

export const suggest = (pid: string, body: { trade: string; submittal_doc_ids: string[]; governing_doc_ids?: string[] }) =>
  api<{ suggested: string[]; cost: Cost }>(`${P(pid)}/suggest`, { method: "POST", body: JSON.stringify(body) });

export const startReview = (
  pid: string,
  body: {
    name: string;
    trade: string;
    supplier?: string;
    revision: string;
    submittal_doc_ids: string[];
    governing_doc_ids: string[];
    instructions?: string;
    approve_usd: number;
    milestone_id?: string | null;
  },
) => api<SubmittalRow>(P(pid), { method: "POST", body: JSON.stringify(body) });

export const rerun = (pid: string, sid: string, approve_usd: number) =>
  api<SubmittalRow>(`${P(pid)}/${sid}/run`, { method: "POST", body: JSON.stringify({ approve_usd }) });

export const setCall = (
  pid: string,
  sid: string,
  fid: string,
  body: { call: Call | null; call_note?: string | null; question?: string; ask_who?: Party },
) => api<{ finding: Finding; status: SubmittalStatus; gate: Gate }>(`${P(pid)}/${sid}/findings/${fid}`, {
  method: "PATCH",
  body: JSON.stringify(body),
});

export const patchSubmittal = (
  pid: string,
  sid: string,
  body: { outcome?: Outcome | null; outcome_note?: string | null; signed_received?: boolean; milestone_id?: string | null },
) => api<SubmittalDetail>(`${P(pid)}/${sid}`, { method: "PATCH", body: JSON.stringify(body) });

export const getEmail = (pid: string, sid: string, party: Party) =>
  api<SubmittalEmail>(`${P(pid)}/${sid}/emails/${party}`);
export const saveEmail = (
  pid: string,
  sid: string,
  party: Party,
  body: { to_name: string; to_email: string; subject: string; body: string },
) => api<SubmittalEmail>(`${P(pid)}/${sid}/emails/${party}`, { method: "PUT", body: JSON.stringify(body) });
export const markCopied = (pid: string, sid: string, party: Party) =>
  api<{ status: SubmittalStatus }>(`${P(pid)}/${sid}/emails/${party}/copied`, { method: "POST" });

export const newRevision = (
  pid: string,
  sid: string,
  body: { revision: string; submittal_doc_ids: string[]; instructions?: string; approve_usd: number },
) => api<SubmittalRow>(`${P(pid)}/${sid}/revision`, { method: "POST", body: JSON.stringify(body) });

/** The subject as it goes out: the PM's CURRENT text plus the [KS-Sn] tag. Built
 *  here, not taken from the server's copy at open time, so an edited subject
 *  still matches what is saved and the BCC copy is filed as ours (audit 2026-10-17). */
export function taggedSubject(subject: string, ref: string): string {
  // Same rule as services/milestones.tag_subject: drop any tag, trim, append.
  return `${subject.replace(/\[KS-[MS]\d+\]/gi, "").trim()} [KS-${ref}]`;
}

export function gmailHref(e: SubmittalEmail, ref: string): string {
  return `https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(e.to_email ?? "")}&bcc=${encodeURIComponent(
    e.project_address ?? "",
  )}&su=${encodeURIComponent(taggedSubject(e.subject, ref))}&body=${encodeURIComponent(e.body)}`;
}

export const getCitation = (pid: string, sid: string, fid: string, side: "shop" | "plan") =>
  api<Citation>(`${P(pid)}/${sid}/findings/${fid}/cite?side=${side}`);

export const quoteLineToLibrary = (
  pid: string,
  sid: string,
  line: number,
  body: { name: string; unit: string; unit_cost: number; trade?: string | null; sku?: string | null },
) =>
  api<{ material_id: string; line: number }>(`${P(pid)}/${sid}/quote-lines/${line}/to-library`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const saveChecklist = (
  key: string,
  body: {
    label: string;
    engineer_role: string;
    checks: { key?: string; label: string; what: string }[];
    governing: string[];
    governing_words: string[];
  },
) => api<Checklist>(`/submittal-checklists/${key}`, { method: "PUT", body: JSON.stringify(body) });

export const resetChecklist = (key: string) =>
  api<{ reset: string; builtin: boolean }>(`/submittal-checklists/${key}`, { method: "DELETE" });
