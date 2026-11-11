import { apiError } from "./apiError";
// Daily site reports — schedule-driven on-site logs + schedule-impact warnings.

const BASE = "/api/backend";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) throw await apiError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export type TaskStatus =
  | "not_started" | "in_progress" | "completed" | "delayed" | "not_worked";

export const TASK_STATUSES: TaskStatus[] = [
  "not_started", "in_progress", "completed", "delayed", "not_worked",
];
export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  not_started: "Not started", in_progress: "In progress",
  completed: "Complete", delayed: "Delayed", not_worked: "Not worked",
};

export interface DailyTask {
  activity_code: string | null;
  name: string | null;
  phase: string | null;
  scheduled_start: string | null;
  scheduled_finish: string | null;
  is_critical?: boolean;
  is_milestone?: boolean;
  from_schedule?: boolean;
  ad_hoc?: boolean;
  removed?: boolean;
  status: TaskStatus;
  percent_complete: number;
  crew: string | null;
  notes: string;
}

export interface DailyReportPayload {
  conditions?: { weather?: string; temp_c?: string | number | null; ground?: string };
  manpower?: { trade?: string; company?: string; count?: number | string; hours?: number | string }[];
  tasks?: DailyTask[];
  deliveries?: { item?: string; supplier?: string; notes?: string }[];
  visitors?: { name?: string; company?: string; purpose?: string }[];
  safety?: { incident?: boolean; notes?: string };
  delays?: { reason?: string; impact?: string; notes?: string }[];
  notes?: string;
  photos?: string[]; // report-level "Site Photos" (Document ids), not tied to a task
  /** What the write-up raised for the office. Kept, not just shown once. */
  raised_for_office?: { what: string; why: string }[];
  /** Sections the spoken report never covered — NOT the same as "none". */
  not_recorded?: string[];
}

export interface DailyReport {
  id: string;
  project_id: string;
  schedule_id: string | null;
  report_date: string;
  status: "draft" | "submitted" | "finalized";
  payload: DailyReportPayload;
  submitted_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  schedule_drifted: boolean;
  task_count?: number;
  completed_count?: number;
}

export interface ImpactWarning {
  source: "slip" | "note";
  severity: "high" | "medium" | "low";
  critical_path?: boolean;
  activity?: string;
  activity_code?: string;
  issue: string;
  impact?: string;
  impacted_upcoming?: string[];
  source_date?: string;
}

export interface ImpactResult {
  warnings: ImpactWarning[];
  generated_for: string;
  count: number;
  has_schedule: boolean;
}

export const listDailyReports = (projectId: string) =>
  api<DailyReport[]>(`/projects/${projectId}/daily-reports`);

export const syncDailyReport = (projectId: string, date: string) =>
  api<DailyReport>(`/projects/${projectId}/daily-reports/sync`, {
    method: "POST",
    body: JSON.stringify({ date }),
  });

export const getDailyReport = (rid: string) =>
  api<DailyReport>(`/daily-reports/${rid}`);

export const updateDailyReport = (
  rid: string,
  body: { payload?: DailyReportPayload; status?: string },
) => api<DailyReport>(`/daily-reports/${rid}`, { method: "PATCH", body: JSON.stringify(body) });

export const submitDailyReport = (rid: string) =>
  api<DailyReport>(`/daily-reports/${rid}/submit`, { method: "POST" });

export const scheduleImpact = (projectId: string) =>
  api<ImpactResult>(`/projects/${projectId}/schedule-impact`);

export const dailyReportPdfUrl = (rid: string) => `/api/daily-report-pdf/${rid}`;
