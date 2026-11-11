/** The Money tab summary — backend services/money. */
import { workspaceGet, workspacePost } from "@/lib/workspaceApi";

export interface MoneyChangeOrder {
  id: string;
  number: number;
  title: string;
  amount: number;
  days: number | null;
  status: "draft" | "pending" | "approved" | "rejected";
  waiting_days: number | null;
  submitted_at: string | null;
}

export interface MoneyInvoice {
  id: string;
  number: string;
  type: string;
  for: string;
  milestone: string | null;
  total: number;
  paid: number;
  /** Derived: "overdue" when sent and past its due date, whatever is stored. */
  status: string;
  stored_status: string;
  due: string | null;
  sent_at: string | null;
}

export interface MoneySummary {
  contract: { amount: number; source: "contract" | "estimate"; with_gst: number; status?: string; version_number?: number } | null;
  change_orders: { approved_before_gst: number; approved_with_gst: number; items: MoneyChangeOrder[] };
  revised_contract_with_gst: number | null;
  invoiced: number;
  paid: number;
  owing: number;
  invoices: MoneyInvoice[];
  schedule_count: number;
  next_milestone: {
    index: number;
    milestone: string;
    pct: number | null;
    amount_before_gst: number | null;
    amount_with_gst: number | null;
  } | null;
}

const base = (pid: string) => `/projects/${encodeURIComponent(pid)}/money`;

export const getMoney = (pid: string) => workspaceGet<MoneySummary>(base(pid));
export const prepareNextInvoice = (pid: string) =>
  workspacePost<{ invoice_id: string; number: string; milestone: string }>(`${base(pid)}/prepare-next`);
export const remindChangeOrder = (pid: string, coId: string) =>
  workspacePost<{ reminded: boolean }>(`${base(pid)}/change-orders/${coId}/remind`);
