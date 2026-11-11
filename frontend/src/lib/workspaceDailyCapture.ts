// Speaking a site day into a daily report, including when there is no signal.
//
// Three jobs: hold what was said until it can be sent, ask the model to propose
// a structured report from it, and keep the ONE-REPORT-PER-DAY rule true while
// doing both.

import { workspaceGet, workspacePost } from "@/lib/workspaceApi";
import type { DailyReportPayload, DailyTask } from "@/lib/dailyReports";

const QUEUE_KEY = "brio.workspace.dailyCaptureQueue.v1";

export interface QueuedCapture {
  projectId: string;
  projectName: string;
  /** ISO date, the report's calendar day. */
  date: string;
  transcript: string;
  savedAt: string;
}

/**
 * ⚠️ ONE REPORT PER PROJECT PER CALENDAR DAY IS A DATABASE CONSTRAINT —
 * `uq_daily_report_project_date`. A queue that can hold two entries for the same
 * day would either fail on sync or, worse, overwrite the first account of the
 * day with the second. So the queue is a MAP keyed on project+date and a second
 * entry APPENDS to the first rather than replacing it: somebody who describes
 * the morning, loses signal, and describes the afternoon has told you about one
 * day, not two.
 */
function key(projectId: string, date: string): string {
  return `${projectId}:${date}`;
}

function readQueue(): Record<string, QueuedCapture> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(QUEUE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    // A corrupt queue must not break the page — but it also must not be
    // silently discarded, so the caller is told it is empty and the raw value
    // is left on disk for anyone debugging.
    return {};
  }
}

function writeQueue(q: Record<string, QueuedCapture>): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
    return true;
  } catch {
    // Quota, or private browsing. Reported rather than swallowed: a supervisor
    // who believes their day is saved and finds it gone is the worst outcome
    // this module can produce.
    return false;
  }
}

/** Append to the day's queued account. Returns false if it could not be stored. */
export function queueTranscript(
  projectId: string, projectName: string, date: string, transcript: string,
): boolean {
  const text = transcript.trim();
  if (!text) return true;
  const q = readQueue();
  const k = key(projectId, date);
  const existing = q[k];
  q[k] = {
    projectId,
    projectName,
    date,
    transcript: existing ? `${existing.transcript.trim()}\n\n${text}` : text,
    savedAt: new Date().toISOString(),
  };
  return writeQueue(q);
}

export function listQueued(): QueuedCapture[] {
  return Object.values(readQueue()).sort((a, b) => (a.savedAt < b.savedAt ? 1 : -1));
}

export function getQueued(projectId: string, date: string): QueuedCapture | null {
  return readQueue()[key(projectId, date)] ?? null;
}

export function clearQueued(projectId: string, date: string): void {
  const q = readQueue();
  delete q[key(projectId, date)];
  writeQueue(q);
}

/** Whether the browser believes it can reach the network.
 *
 *  `navigator.onLine` is famously optimistic — it reports a connection to a
 *  router that reaches nothing. It is used only to DECIDE WHAT TO OFFER, never
 *  to decide whether something saved: a send that fails falls back to the queue
 *  regardless of what this says. */
export function probablyOnline(): boolean {
  if (typeof navigator === "undefined") return true;
  return navigator.onLine !== false;
}

// ── the proposal job ────────────────────────────────────────────────

export interface CaptureProposal {
  payload: Partial<DailyReportPayload>;
  /** Sections the transcript never covered. NOT the same as "nothing happened". */
  not_recorded: string[];
  flagged_for_office: { what: string; why: string }[];
  unclear: string[];
}

export interface CaptureJob {
  /** Per-step progress on a long job. Empty for kinds that finish in seconds. */
  steps?: import("@/components/JobProgress").JobStep[];
  progress?: import("@/components/JobProgress").JobProgressSummary;
  id: string;
  status: "queued" | "running" | "done" | "error";
  error: string | null;
  output: { text?: string; proposal?: CaptureProposal } | null;
  created_at: string;
}

export function createCapture(
  projectId: string,
  date: string,
  scheduledTasks: DailyTask[],
  transcript: string,
): Promise<CaptureJob> {
  return workspacePost<CaptureJob>("/ai-jobs", {
    project_id: projectId,
    kind: "daily_report_capture",
    input: {
      report_date: date,
      scheduled_tasks: scheduledTasks,
      transcript,
    },
  });
}

export function getCapture(jobId: string): Promise<CaptureJob> {
  return workspaceGet<CaptureJob>(`/ai-jobs/${encodeURIComponent(jobId)}`);
}

/** Human labels for the eight payload sections. */
export const SECTION_LABEL: Record<string, string> = {
  conditions: "Conditions",
  manpower: "Manpower",
  tasks: "Tasks",
  deliveries: "Deliveries",
  visitors: "Visitors",
  safety: "Safety",
  delays: "Delays",
  notes: "General notes",
};

export const ALL_SECTIONS = Object.keys(SECTION_LABEL);

/**
 * Merge a confirmed proposal into the report's existing payload.
 *
 * ⚠️ SECTIONS THE PROPOSAL DID NOT TOUCH ARE LEFT ALONE. The proposal only
 * carries what was spoken about; overwriting the whole payload with it would
 * erase anything already entered by hand — which is the shape of data loss
 * nobody notices until the report is read back.
 *
 * Tasks merge BY activity_code so a spoken update lands on the scheduled row
 * rather than appending a duplicate beside it.
 */
export function mergeProposal(
  current: DailyReportPayload,
  proposal: Partial<DailyReportPayload>,
): DailyReportPayload {
  const next: DailyReportPayload = { ...current };

  for (const [k, v] of Object.entries(proposal)) {
    if (v === undefined || v === null) continue;
    if (k === "tasks") continue; // handled below
    (next as Record<string, unknown>)[k] = v;
  }

  const spoken = proposal.tasks ?? [];
  if (spoken.length) {
    const rows = [...(current.tasks ?? [])];
    for (const t of spoken) {
      const code = t.activity_code;
      const i = code ? rows.findIndex((r) => r.activity_code === code) : -1;
      if (i >= 0) {
        // Keep the scheduled fields; take only what the supervisor reported.
        rows[i] = {
          ...rows[i],
          status: t.status ?? rows[i].status,
          percent_complete: t.percent_complete ?? rows[i].percent_complete,
          crew: t.crew ?? rows[i].crew,
          notes: t.notes || rows[i].notes,
        };
      } else {
        rows.push({ ...t, from_schedule: false, ad_hoc: true, removed: false } as DailyTask);
      }
    }
    next.tasks = rows;
  }

  return next;
}


/**
 * Merge a confirmed write-up into the report AND keep what the review showed.
 *
 * `mergeProposal` alone dropped two things the reviewer was shown: what was
 * raised for the office, and which sections were never mentioned. Both are now
 * stored on the report (`raised_for_office`, `not_recorded`), and photos taken
 * while describing the day are attached to the report itself, not only named
 * in the transcript.
 */
/** True when a report section holds something a person said. */
export function hasContent(v: unknown): boolean {
  if (v == null || v === "") return false;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "object") return Object.values(v as Record<string, unknown>).some(hasContent);
  return true;
}

export function mergeCapture(
  current: DailyReportPayload,
  proposal: CaptureProposal,
  photoIds: string[] = [],
): DailyReportPayload {
  const next = mergeProposal(current, proposal.payload);
  const raised = [...(current.raised_for_office ?? []), ...proposal.flagged_for_office];
  if (raised.length) next.raised_for_office = raised;
  // A section the report already HAS stays recorded; the empty [] / {} a new
  // report starts with does not count. Treating those as recorded dropped
  // visitors, delays, safety and conditions from not_recorded, so they printed
  // as "None." (rollout test 2026-10-14).
  next.not_recorded = proposal.not_recorded.filter(
    (s) => !(s in proposal.payload) && !hasContent((current as Record<string, unknown>)[s]),
  );
  if (photoIds.length) next.photos = Array.from(new Set([...(current.photos ?? []), ...photoIds]));
  return next;
}
