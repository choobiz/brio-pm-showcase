// Client selections & approvals — office side, via the /api/backend proxy.
import { workspaceGet, workspacePost } from "@/lib/workspaceApi";

const BASE = "/api/backend";

export interface SelectionOption {
  id: string;
  label: string;
  description: string | null;
  price_delta: number | null;
}

export interface ClientSelection {
  id: string;
  title: string;
  category: string | null;
  description: string | null;
  decision_type: "select" | "approve";
  purchaser: "brio" | "client" | null;
  options: SelectionOption[];
  status: "pending" | "submitted" | "confirmed";
  selected_option_id: string | null;
  selected_option: SelectionOption | null;
  client_comment: string | null;
  decided_at: string | null;
  due_date: string | null;
  display_order: number;
  created_at: string | null;
  activity_code: string | null;
  lead_time_days: number | null;
  allowance: number | null;
  sent_at: string | null;
  change_order_id: string | null;
}

export interface SelectionInput {
  title: string;
  category?: string | null;
  description?: string | null;
  decision_type?: "select" | "approve";
  purchaser?: "brio" | "client" | null;
  options?: { id?: string; label: string; description?: string | null; price_delta?: number | null }[];
  due_date?: string | null;
  display_order?: number;
  status?: "pending" | "submitted" | "confirmed";
  activity_code?: string | null;
  lead_time_days?: number | null;
  allowance?: number | null;
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

const base = (pid: string) => `/projects/${pid}/selections`;

export const listSelections = (pid: string) => api<ClientSelection[]>(base(pid));

export const createSelection = (pid: string, body: SelectionInput) =>
  api<ClientSelection>(base(pid), { method: "POST", body: JSON.stringify(body) });

export const updateSelection = (pid: string, sid: string, body: SelectionInput) =>
  api<ClientSelection>(`${base(pid)}/${sid}`, { method: "PATCH", body: JSON.stringify(body) });

export const deleteSelection = (pid: string, sid: string) =>
  api<void>(`${base(pid)}/${sid}`, { method: "DELETE" });

// Planned against the schedule (R2Selections, 2026-11-02).
export type PlanState = "not_sent" | "waiting" | "chose" | "confirmed";
export interface PlannedSelection extends ClientSelection {
  activity: { code: string; name: string; start: string } | null;
  decide_by: string | null;
  late: boolean;
  state: PlanState;
  days_waiting: number | null;
  choice_delta: number | null;
}
export interface SelectionPlan {
  selections: PlannedSelection[];
  waiting: number;
  activities: { code: string; name: string; start: string }[];
}
export interface SelectionSuggestion {
  title: string; activity_code: string; activity_name: string; start: string; lead_time_days: number;
}
export const getSelectionPlan = (pid: string) => workspaceGet<SelectionPlan>(`${base(pid)}/plan`);
export const getSelectionSuggestions = (pid: string) => workspaceGet<SelectionSuggestion[]>(`${base(pid)}/suggestions`);
export const sendSelection = (pid: string, sid: string) => workspacePost<ClientSelection>(`${base(pid)}/${sid}/send`);
export const confirmSelection = (pid: string, sid: string) =>
  workspacePost<ClientSelection & { change_order: { id: string; number: number; cost_impact: number } | null }>(
    `${base(pid)}/${sid}/confirm`);
