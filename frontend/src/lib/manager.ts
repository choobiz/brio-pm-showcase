// Manager dashboard — client-side via the /api/backend proxy.

const BASE = "/api/backend";

export interface HealthCard {
  project_id: string;
  name: string;
  address: string | null;
  status: string;
  schedule: {
    target_finish: string | null;
    baseline_finish: string | null;
    days_delta: number;
    light: "green" | "amber" | "red";
  } | null;
  hours: { total_hm: string; open_punches: number; offsite_count: number };
  reports: { last_report_date: string | null; missing: boolean };
  package_stale: boolean;
  package_sent: boolean;
  contract_total: number;
  paid_to_date: number;
  risk_count: number;
  pending_change_orders: number;
}

export interface Alert {
  project_id: string;
  project_name: string;
  severity: "high" | "medium" | "low";
  source: string;
  activity: string | null;
  issue: string;
  critical_path?: boolean;
  impacted_upcoming?: string[];
}

export interface ReportFeedItem {
  id: string;
  project_id: string;
  project_name: string;
  report_date: string;
  status: string;
  submitted: boolean;
}

async function api<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json() as Promise<T>;
}

export interface ActionItem {
  type: "selection" | "message" | "change_order";
  project_id: string;
  project_name: string;
  label: string;
  detail: string;
  at: string | null;
  href: string;
}

export const getPortfolio = () => api<{ projects: HealthCard[] }>("/manager/portfolio");
export const getAlerts = () => api<{ alerts: Alert[]; count: number }>("/manager/alerts");
export const getReportFeed = () => api<{ reports: ReportFeedItem[] }>("/manager/report-feed");
export const getActionQueue = () => api<{ items: ActionItem[]; count: number }>("/manager/action-queue");

export interface CommsRow {
  project_id: string;
  project_name: string;
  total_messages: number;
  last_message_at: string | null;
  last_sender: "owner" | "office" | null;
  last_office_at: string | null;
  unread_from_client: number;
  awaiting_reply: boolean;
}
export const getCommsLog = () => api<{ projects: CommsRow[] }>("/manager/comms-log");
