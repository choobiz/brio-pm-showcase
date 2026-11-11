// Offline layer for the field app (Slice 2): a small IndexedDB store for the
// current report draft + a mutation outbox, so the editor saves local-first and
// flushes to the server when a connection returns. No external deps.

import {
  type DailyReport,
  type DailyReportPayload,
  type DailyTask,
  getDailyReport,
  submitDailyReport,
  updateDailyReport,
} from "@/lib/dailyReports";
import { uploadDocument } from "@/lib/documents";

const DB_NAME = "brio-field";
const DB_VERSION = 2;

export interface OutboxItem {
  reportId: string;
  payload: DailyReportPayload;
  submit?: boolean;
  updatedAt: number;
}

export interface PhotoItem {
  key?: number;
  reportId: string;
  projectId: string;
  taskCode: string | null; // activity_code (stable), else null for ad-hoc
  taskIndex: number; // fallback match
  filename: string;
  mime: string;
  blob: Blob;
  createdAt: number;
}

function hasIDB(): boolean {
  return typeof window !== "undefined" && "indexedDB" in window;
}

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("reports")) {
        const s = db.createObjectStore("reports", { keyPath: "id" });
        s.createIndex("project_date", ["project_id", "report_date"], { unique: false });
      }
      if (!db.objectStoreNames.contains("outbox")) {
        db.createObjectStore("outbox", { keyPath: "reportId" });
      }
      if (!db.objectStoreNames.contains("photos")) {
        db.createObjectStore("photos", { keyPath: "key", autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function run<T>(
  store: string,
  mode: IDBTransactionMode,
  fn: (s: IDBObjectStore) => IDBRequest,
): Promise<T> {
  return openDB().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(store, mode);
        const req = fn(t.objectStore(store));
        req.onsuccess = () => resolve(req.result as T);
        req.onerror = () => reject(req.error);
        t.oncomplete = () => db.close();
      }),
  );
}

// ── report cache ────────────────────────────────────────────────────

export async function cacheReport(r: DailyReport): Promise<void> {
  if (!hasIDB()) return;
  try {
    await run("reports", "readwrite", (s) => s.put(r));
  } catch {
    /* offline cache is best-effort */
  }
}

export async function getCachedByProjectDate(
  projectId: string,
  date: string,
): Promise<DailyReport | undefined> {
  if (!hasIDB()) return undefined;
  try {
    return await run<DailyReport | undefined>("reports", "readonly", (s) =>
      s.index("project_date").get([projectId, date]),
    );
  } catch {
    return undefined;
  }
}

// ── outbox ──────────────────────────────────────────────────────────

export async function queueMutation(item: OutboxItem): Promise<void> {
  if (!hasIDB()) return;
  await run("outbox", "readwrite", (s) => s.put(item));
}

export async function getPending(reportId: string): Promise<OutboxItem | undefined> {
  if (!hasIDB()) return undefined;
  try {
    return await run<OutboxItem | undefined>("outbox", "readonly", (s) => s.get(reportId));
  } catch {
    return undefined;
  }
}

export async function pendingCount(): Promise<number> {
  if (!hasIDB()) return 0;
  try {
    return await run<number>("outbox", "readonly", (s) => s.count());
  } catch {
    return 0;
  }
}

async function clearPending(reportId: string): Promise<void> {
  await run("outbox", "readwrite", (s) => s.delete(reportId));
}

async function allPending(): Promise<OutboxItem[]> {
  try {
    return (await run<OutboxItem[]>("outbox", "readonly", (s) => s.getAll())) || [];
  } catch {
    return [];
  }
}

/** Send every queued mutation in order; stop on the first failure (stays queued
 *  for the next attempt). Returns how many flushed and whether any remain. */
export async function flushOutbox(): Promise<{ sent: number; remaining: number }> {
  if (!hasIDB()) return { sent: 0, remaining: 0 };
  const items = await allPending();
  let sent = 0;
  for (const it of items) {
    try {
      let latest = await updateDailyReport(it.reportId, { payload: it.payload });
      if (it.submit) latest = await submitDailyReport(it.reportId);
      await cacheReport(latest);
      await clearPending(it.reportId);
      sent++;
    } catch {
      return { sent, remaining: items.length - sent };
    }
  }
  return { sent, remaining: 0 };
}

// ── photo queue ─────────────────────────────────────────────────────

export async function queuePhoto(item: Omit<PhotoItem, "key">): Promise<void> {
  if (!hasIDB()) return;
  await run("photos", "readwrite", (s) => s.add(item));
}

async function allPhotos(): Promise<PhotoItem[]> {
  if (!hasIDB()) return [];
  try {
    return (await run<PhotoItem[]>("photos", "readonly", (s) => s.getAll())) || [];
  } catch {
    return [];
  }
}

/** Pending (not-yet-uploaded) photo count per task for a report, keyed by the
 *  task's activity_code (or "idx:<n>" for ad-hoc), so the UI can show a badge. */
export async function pendingPhotoCounts(reportId: string): Promise<Record<string, number>> {
  const out: Record<string, number> = {};
  for (const ph of await allPhotos()) {
    if (ph.reportId !== reportId) continue;
    const k = ph.taskCode ?? `idx:${ph.taskIndex}`;
    out[k] = (out[k] || 0) + 1;
  }
  return out;
}

/** Upload each queued photo to R2, then link its Document id into the matching
 *  task of the (latest server) report. Fail-stop, same as the outbox. */
export async function flushPhotos(): Promise<{ sent: number; remaining: number }> {
  if (!hasIDB()) return { sent: 0, remaining: 0 };
  const items = await allPhotos();
  let sent = 0;
  for (const ph of items) {
    try {
      const file = new File([ph.blob], ph.filename || "photo.jpg", {
        type: ph.mime || "image/jpeg",
      });
      const doc = await uploadDocument(ph.projectId, file, "photo");
      const report = await getDailyReport(ph.reportId);
      const payload = (report.payload || {}) as DailyReportPayload;
      let nextPayload: DailyReportPayload;
      if (ph.taskCode === "__report__") {
        // Report-level "Site Photo".
        nextPayload = { ...payload, photos: [...(payload.photos || []), doc.id] };
      } else {
        const tasks = [...((payload.tasks || []) as (DailyTask & { photos?: string[] })[])];
        let ti = ph.taskCode ? tasks.findIndex((t) => t.activity_code === ph.taskCode) : -1;
        if (ti < 0) ti = ph.taskIndex;
        if (ti < 0 || !tasks[ti]) {
          // Task no longer present — drop the blob rather than fail forever.
          await run("photos", "readwrite", (s) => s.delete(ph.key!));
          sent++;
          continue;
        }
        tasks[ti] = { ...tasks[ti], photos: [...(tasks[ti].photos || []), doc.id] };
        nextPayload = { ...payload, tasks };
      }
      const updated = await updateDailyReport(ph.reportId, { payload: nextPayload });
      await cacheReport(updated);
      await run("photos", "readwrite", (s) => s.delete(ph.key!));
      sent++;
    } catch {
      return { sent, remaining: items.length - sent };
    }
  }
  return { sent, remaining: 0 };
}
