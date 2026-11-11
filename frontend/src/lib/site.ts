/** The Site tab's one-day view. Read-only: it never creates a report. */
import type { DailyReport } from "@/lib/dailyReports";
import { workspaceGet } from "@/lib/workspaceApi";

export type DayStatus = "filed" | "draft" | "missing" | "pending" | "quiet" | "future";

export interface SiteHours {
  id: string;
  user: string | null;
  punch_in_at: string;
  punch_out_at: string | null;
  hours: number | null;
  status: string;
  location_flag: boolean;
  distance_in_m: number | null;
}

export interface SiteDay {
  date: string;
  today: string;
  week: { date: string; status: DayStatus }[];
  has_schedule: boolean;
  scheduled: {
    activity_code: string | null;
    name: string;
    is_critical: boolean;
    is_milestone: boolean;
    start_date: string;
    finish_date: string;
  }[];
  report: DailyReport | null;
  next_milestone: { label: string; date: string } | null;
  hours: SiteHours[];
  offsite_count: number;
  photos: { id: string; filename: string }[];
}

export function getSiteDay(projectId: string, day?: string): Promise<SiteDay> {
  return workspaceGet<SiteDay>(
    `/projects/${encodeURIComponent(projectId)}/site-day${day ? `?day=${day}` : ""}`,
  );
}
