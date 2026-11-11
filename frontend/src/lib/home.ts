// Home (across every job) and the road to an estimate (one project).
// Read-only views over existing records; see backend app/services/home.py.

import type { ActionItem } from "@/lib/manager";
import type { RegisterItem } from "@/lib/register";
import { workspaceGet } from "@/lib/workspaceApi";

export interface ContractValue {
  amount: number;
  /** "contract" = the contract record; "estimate" = no contract yet, so the
   *  active estimate stands in and the page must say so. Both before GST. */
  source: "contract" | "estimate";
  version_number?: number;
  status?: string;
  before_gst: true;
}

export interface ActiveEstimate {
  id: string;
  version_number: number;
  label: string | null;
  created_at: string | null;
  detail_level: string | null;
  total_before_gst: number;
  total_with_gst: number;
}

export interface ActiveJob {
  project_id: string;
  name: string;
  address: string | null;
  status: string;
  type: string | null;
  /** Why it is on Home: its status says under way, or someone worked on it lately. */
  why_active: "status" | "recent_work";
  last_activity_at: string | null;
  next_milestone: { label: string; date: string } | null;
  estimate: Pick<ActiveEstimate, "version_number" | "total_with_gst" | "total_before_gst"> | null;
  contract: ContractValue | null;
}

export interface WeekItem {
  project_id: string;
  project_name: string;
  name: string;
  phase: string | null;
  milestone: boolean;
  event: "starts" | "finishes" | "milestone";
  date: string;
}

export interface Home {
  today: string;
  needs_you: { register: RegisterItem[]; client: ActionItem[] };
  worth_a_look: RegisterItem[];
  active_jobs: ActiveJob[];
  active_rule: string;
  this_week: WeekItem[];
}

export const getHome = () => workspaceGet<Home>("/home");

export type StepState = "done" | "todo" | "not_needed";

export interface RoadStep {
  key: "documents" | "scope" | "drawings_read" | "checked" | "signed_off" | "estimate";
  label: string;
  state: StepState;
  detail: string;
  href: string;
  current: boolean;
}

export interface Road {
  steps: RoadStep[];
  current: RoadStep["key"] | null;
  snapshot: {
    documents: number;
    scope_versions: number;
    estimate: ActiveEstimate | null;
    estimate_versions: number;
    contract: ContractValue | null;
    invoices: { count: number; total: number; paid: number };
  };
}

export const getRoad = (projectId: string) =>
  workspaceGet<Road>(`/projects/${encodeURIComponent(projectId)}/road`);

/** "Mon 4 Nov" — schedule dates are calendar dates, so no timezone shift. */
export function dayLabel(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-CA", { weekday: "short", day: "numeric", month: "short" });
}
