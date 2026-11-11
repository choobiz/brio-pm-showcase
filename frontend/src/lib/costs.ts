/** Money › Costs — backend services/costs. Bills BRIO receives, and the budget per division. */
import { workspaceDelete, workspaceGet, workspacePost, workspacePut } from "@/lib/workspaceApi";

export type CostKind = "supplier_invoice" | "subtrade_invoice" | "other";

export interface Bill {
  id: string;
  kind: CostKind;
  vendor: string;
  invoice_number: string | null;
  division: string | null;
  description: string | null;
  /** Before GST. */
  amount: number;
  gst: number;
  cost_date: string | null;
  source_document_id: string | null;
  created_at: string | null;
}

export interface DivisionLine {
  division: string;
  budget: number | null;
  spent: number;
  left: number | null;
  over: boolean;
}

export interface CostSummary {
  divisions: string[];
  kinds: CostKind[];
  contract: {
    base: number;
    approved_change_orders: { number: number; title: string; amount: number }[];
    revised: number;
  } | null;
  bills: Bill[];
  spent_before_gst: number;
  gst: number;
  unassigned_spent: number;
  budget_total: number | null;
  budget_rows: { id: string; division: string; amount: number; source: string; note: string | null }[];
  by_division: DivisionLine[];
  vendors: string[];
}

export interface BillInput {
  kind: CostKind;
  vendor: string;
  invoice_number?: string | null;
  cost_date: string;
  amount: number;
  gst: number;
  division?: string | null;
  description?: string | null;
  source_document_id?: string | null;
}

export interface BudgetEstimate { id: string; version_number: number; label: string | null; direct_cost: number }

export interface BudgetPreview {
  estimate_version_id: string;
  version_number: number;
  contract_base: number;
  categories: { name: string; direct_cost: number; share: number; amount: number; division: string | null; suggested: string | null }[];
  change_orders: { number: number; title: string; amount: number; division: string | null; suggested: string | null }[];
  divisions: string[];
  complete: boolean;
  total: number;
}

export interface BudgetInput {
  estimate_version_id: string;
  mapping: Record<string, string>;
  co_divisions: Record<string, string>;
}

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/costs`;

export const getCosts = (pid: string) => workspaceGet<CostSummary>(base(pid));
export const addBill = (pid: string, b: BillInput) => workspacePost<Bill>(base(pid), b);
export const editBill = (pid: string, id: string, b: Partial<BillInput>) => workspacePut<Bill>(`${base(pid)}/${id}`, b);
export const deleteBill = (pid: string, id: string) => workspaceDelete(`${base(pid)}/${id}`);
export const budgetEstimates = (pid: string) => workspaceGet<BudgetEstimate[]>(`${base(pid)}/budget/estimates`);
export const previewBudget = (pid: string, b: BudgetInput) => workspacePost<BudgetPreview>(`${base(pid)}/budget/preview`, b);
export const saveBudget = (pid: string, b: BudgetInput) => workspacePut<CostSummary>(`${base(pid)}/budget`, b);
