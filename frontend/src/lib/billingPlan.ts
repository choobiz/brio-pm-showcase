/** Contract › Billing plan — backend services/billing_plan. When each signed
 *  stage is billed; saving builds the schedule of values and the cash flow. */
import { workspaceGet, workspacePost, workspacePut } from "@/lib/workspaceApi";

export interface PlanStage {
  no: number;
  milestone: string;
  pct: number;
  /** Before GST, as signed. */
  amount: number;
  detail: string;
  holdback: boolean;
  activity_code: string | null;
  activity_name: string | null;
  invoice: { number: string; status: string; date: string | null; subtotal: number; paid_at: string | null } | null;
  bill_date: string | null;
  paid_date: string | null;
  needs_pick: boolean;
}

export interface PlanDivision {
  division: string;
  budget: number;
  phases: string[];
  picked: string | null;
  whole_job: boolean;
  needs_pick: boolean;
}

export interface BillingPlan {
  contract: {
    id: string; reference: string; status: string; signed_at: string | null;
    substantial_completion_date: string | null; substantial_completion_actual: string | null; revised: number | null;
  };
  schedule: {
    id: string; version_number: number; client_version: number | null;
    project_start: string | null; expected_substantial_completion: string | null;
  };
  stages: PlanStage[];
  stage_total: number;
  stages_off_by: number | null;
  activities: { code: string; name: string; phase: string | null; finish_date: string; is_milestone: boolean }[];
  phases: string[];
  divisions: PlanDivision[];
  cost_share_pct: number;
  owner_net_days: number;
  holdback_release_days: number;
  complete: boolean;
  problems: string[];
  saved_at: string | null;
  latest_sov: { id: string; version_number: number } | null;
  latest_cash_flow: { id: string; version_number: number } | null;
  saved?: {
    sov: { id: string; version_number: number };
    cash_flow: { id: string; version_number: number; min_cash_position: number;
      min_position_week_end: string | null; bridge_financing_required: number };
  };
}

export interface PlanInput {
  stage_activities?: Record<string, string | null>;
  division_phases?: Record<string, string | null>;
  cost_share_pct?: number;
  owner_net_days?: number;
  substantial_completion_actual?: string | null;
}

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/billing-plan`;

export const getBillingPlan = (pid: string) => workspaceGet<BillingPlan>(base(pid));
export const previewBillingPlan = (pid: string, b: PlanInput) => workspacePost<BillingPlan>(`${base(pid)}/preview`, b);
export const saveBillingPlan = (pid: string, b: PlanInput) => workspacePut<BillingPlan>(base(pid), b);
