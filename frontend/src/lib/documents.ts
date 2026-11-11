// Track B — document upload/list/download/delete.
// Uploads are two-step: presign (our API) → PUT direct to R2 → finalize.
// The R2 PUT bypasses the proxy, so progress is tracked via XHR.

import { workspaceGet, workspacePost, workspacePatch } from "@/lib/workspaceApi";
import { apiError } from "./apiError";

const BASE = "/api/backend";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) throw await apiError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export type DocumentCategory =
  | "drawing"
  | "contract"
  | "scope"
  | "report"
  | "permit"
  | "photo"
  | "client_note"
  | "invoice"
  | "quote"
  | "survey"
  | "correspondence"
  | "video"
  | "financial"
  | "invoice_legacy"
  | "prepared"
  | "other";

export const DOCUMENT_CATEGORIES: { value: DocumentCategory; label: string }[] = [
  { value: "drawing", label: "Drawing" },
  { value: "contract", label: "Contract" },
  { value: "scope", label: "Scope / SoW" },
  { value: "report", label: "Report (SMP / Geotech / Hazmat)" },
  { value: "permit", label: "Permit" },
  { value: "photo", label: "Photo" },
  { value: "client_note", label: "Client Note" },
  // Present in the document store since the 2026-08-31 rebuild. Without these
  // entries the picker cannot even display the category a document already has,
  // let alone let anyone correct it.
  { value: "invoice", label: "Invoice" },
  { value: "quote", label: "Quote / Proposal" },
  { value: "survey", label: "Survey" },
  { value: "correspondence", label: "Correspondence" },
  { value: "video", label: "Video" },
  { value: "financial", label: "Financial / Accounting" },
  { value: "invoice_legacy", label: "Legacy Invoice" },
  // Orders, takeoffs and sheets finalised from Ask → Prepare (phase 9).
  { value: "prepared", label: "Prepared (order / takeoff / sheet)" },
  { value: "other", label: "Other" },
];

export interface DocumentRead {
  id: string;
  project_id: string;
  category: string;
  phase: string;
  filename: string;
  r2_key: string;
  mime: string | null;
  size_bytes: number | null;
  uploaded_by_id: string | null;
  archived: boolean;
  client_visible: boolean;
  /** When a person confirmed the category; null = nobody has. */
  category_confirmed_at: string | null;
  created_at: string;
  updated_at: string;
}

interface PresignResponse {
  document_id: string;
  r2_key: string;
  upload_url: string;
}

export function listDocuments(projectId: string, archived = false): Promise<DocumentRead[]> {
  return api(
    `/documents?project_id=${encodeURIComponent(projectId)}${archived ? "&archived=true" : ""}`,
  );
}

// ── Documents page, round 1 (2026-10-12) ──────────────────────────────────
// These go through workspaceApi so a failure reaches the screen as a sentence,
// not as "API 422: {…}".

export function renameDocument(id: string, filename: string): Promise<DocumentRead> {
  return workspacePatch<DocumentRead>(`/documents/${id}`, { filename });
}

export function setDocumentCategory(id: string, category: string): Promise<DocumentRead> {
  return workspacePatch<DocumentRead>(`/documents/${id}`, { category });
}

/** "This category is right as it is" — records the confirmation, changes nothing else. */
export function confirmDocumentCategory(id: string): Promise<DocumentRead> {
  return workspacePatch<DocumentRead>(`/documents/${id}`, { category_confirmed: true });
}

export function restoreDocument(id: string): Promise<DocumentRead> {
  return workspacePost<DocumentRead>(`/documents/${id}/restore`);
}

export function bulkUpdateDocuments(
  projectId: string,
  ids: string[],
  change: { category?: string; client_visible?: boolean; archived?: boolean },
): Promise<{ updated: number; unchanged: number }> {
  return workspacePost(`/documents/bulk`, { project_id: projectId, document_ids: ids, ...change });
}

/** {document_id: ["Takeoff", "Scope", …]} — only documents used somewhere appear. */
export function getUsedIn(projectId: string): Promise<Record<string, string[]>> {
  return workspaceGet(`/documents/used-in?project_id=${encodeURIComponent(projectId)}`);
}

/** Whether a file's text has been read for searching inside it. Only PDF,
 * Word, Excel and text files have a row; photos and video are never read. */
export type DocumentTextStatus = {
  document_id: string;
  status: "pending" | "running" | "ok" | "no_text" | "too_large" | "failed";
  page_count: number | null;
  pages_with_text: number | null;
  truncated: boolean;
  error: string | null;
};

export function listTextStatus(projectId: string): Promise<DocumentTextStatus[]> {
  return workspaceGet(`/documents/text-status?project_id=${encodeURIComponent(projectId)}`);
}

/** Queue a file to have its text read again. Local, no AI cost. */
export function readTextAgain(documentId: string): Promise<{ status: string }> {
  return workspacePost(`/documents/${documentId}/read-text`, {});
}

export const TEXT_UNREADABLE = new Set(["no_text", "too_large", "failed"]);

/** The server's category guess for each filename, before anything is uploaded. */
export function suggestCategories(filenames: string[]): Promise<Record<string, string>> {
  return workspacePost(`/documents/suggest-categories`, { filenames });
}

// ── P1 indexing ──────────────────────────────────────────────────────
// Per-document status badge for the Documents grid. `status` is one of
// "not_indexed" | "queued" | "running" | "succeeded" | "failed" | "skipped".
export interface DocumentIndexBadge {
  document_id: string;
  status: string;
  summary_short: string | null;
  indexed_at: string | null;
  /** The whole summary — "What the AI read". */
  summary_text?: string | null;
  /** Why reading failed. */
  error_message?: string | null;
}

export function listIndexStatus(
  projectId: string,
): Promise<DocumentIndexBadge[]> {
  return api(
    `/documents/index-status?project_id=${encodeURIComponent(projectId)}`,
  );
}

export function queueDocumentIndex(
  id: string,
  force = false,
): Promise<DocumentIndexBadge> {
  return api(`/documents/${id}/index?force=${force}`, { method: "POST" });
}

export function reindexProject(
  projectId: string,
  force = true,
): Promise<{ queued: number; skipped: number; total: number }> {
  return api(
    `/documents/index/reindex-project?project_id=${encodeURIComponent(
      projectId,
    )}&force=${force}`,
    { method: "POST" },
  );
}

export interface ProjectIndexRead {
  project_id: string;
  project_name: string;
  document_count: number;
  summarized_count: number;
  pending_count: number;
  failed_count: number;
  last_indexed_at: string | null;
  generated_at: string;
  markdown: string;
}

export function getProjectIndex(
  projectId: string,
  opts?: { rebuild?: boolean },
): Promise<ProjectIndexRead> {
  const q = opts?.rebuild ? "&rebuild=true" : "";
  return api(
    `/documents/index?project_id=${encodeURIComponent(projectId)}${q}`,
  );
}

export function materializeProjectIndex(
  projectId: string,
): Promise<{ r2_key: string; download_url: string }> {
  return api(
    `/documents/index/materialize?project_id=${encodeURIComponent(projectId)}`,
    { method: "POST" },
  );
}

export type DocumentPreview = {
  filename: string;
  previewable: boolean;
  content_type?: string | null;
  preview_url?: string | null;
  reason?: string | null;
};

/** A URL the browser renders in place, so a drawing can be looked at without
 *  downloading it. Returns previewable:false with a reason for file types the
 *  browser cannot show (DWG, XLSX, DOCX). */
export function getPreviewUrl(
  documentId: string,
): Promise<DocumentPreview> {
  return api<DocumentPreview>(`/documents/${documentId}/preview`);
}

export function getDownloadUrl(
  id: string,
): Promise<{ filename: string; download_url: string }> {
  return api(`/documents/${id}/download`);
}

// Small server-generated JPEG thumbnail for an image document.
export function getThumbnailUrl(
  id: string,
): Promise<{ thumbnail_url: string }> {
  return api(`/documents/${id}/thumbnail`);
}

export function deleteDocument(id: string): Promise<void> {
  return api(`/documents/${id}`, { method: "DELETE" });
}

// PUT the file straight to R2 via the presigned URL, reporting 0..1 progress.
function uploadToR2(
  url: string,
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`R2 upload failed: ${xhr.status}`));
    xhr.onerror = () => reject(new Error("R2 upload network error"));
    xhr.send(file);
  });
}

// ── ZIP extraction (client-side) ─────────────────────────────────────────
// We extract in the browser instead of POSTing the archive to our Vercel
// proxy because Vercel's serverless functions hard-cap request bodies at
// 4.5 MB (FUNCTION_PAYLOAD_TOO_LARGE) — a real Drive batch download blows
// past that instantly. Each extracted member is then uploaded via the
// existing presign → direct-to-R2 path, which never touches Vercel.
//
// The backend's POST /documents/process-zip endpoint still exists for
// CLI / API callers; the UI never hits it.

export interface ExtractedZipEntry {
  filename: string;
  mime: string;
  blob: Blob;
  suggestedCategory: DocumentCategory;
}

// Filename-based category heuristics. Mirrors backend _CATEGORY_HEURISTICS.
// Order matters: first match wins. Keep these two lists in sync.
const _CATEGORY_RULES: { re: RegExp; cat: DocumentCategory }[] = [
  // Drawings — discipline tags + sheet refs
  { re: /\b(arch|architectural)\b|\ba\d+[-.]?\d*\b/i, cat: "drawing" },
  { re: /\b(struct|structural)\b|\bs\d+[-.]?\d*\b/i, cat: "drawing" },
  { re: /\b(mech|mechanical|hvac)\b|\bm\d+[-.]?\d*\b/i, cat: "drawing" },
  { re: /\b(elec|electrical)\b|\be\d+[-.]?\d*\b/i, cat: "drawing" },
  { re: /\b(plumb|plumbing)\b|\bp\d+[-.]?\d*\b/i, cat: "drawing" },
  { re: /drawing|\bdwg\b|\bplan\b|elevation|section|site\s*plan|floor\s*plan/i, cat: "drawing" },
  // Reports
  { re: /\bsmp\b|stormwater|geotech|hazmat|asbestos/i, cat: "report" },
  { re: /\breport\b|\baudit\b|\bsurvey\b|inspection\s*report/i, cat: "report" },
  // Contract
  { re: /contract|agreement|ccdc|\bsigned\b/i, cat: "contract" },
  // Scope
  { re: /\bscope\b|specification|\bspec\b|\bsow\b|requirements/i, cat: "scope" },
  // Permit
  { re: /permit|approval|licen[cs]e/i, cat: "permit" },
  // Photo by extension (after content-type check below)
  { re: /\.(jpe?g|png|heic|tiff?|gif|webp|bmp)$/i, cat: "photo" },
];

function _suggestCategory(filename: string, mime: string): DocumentCategory {
  if (mime.startsWith("image/")) return "photo";
  for (const { re, cat } of _CATEGORY_RULES) if (re.test(filename)) return cat;
  return "other";
}

// Map a basename to a best-guess MIME — JSZip doesn't carry the original
// Content-Type, and the browser's File constructor needs *something* for
// the eventual R2 PUT to round-trip correctly.
function _guessMime(name: string): string {
  const lower = name.toLowerCase();
  const m: Record<string, string> = {
    pdf: "application/pdf",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    png: "image/png",
    gif: "image/gif",
    webp: "image/webp",
    heic: "image/heic",
    tif: "image/tiff",
    tiff: "image/tiff",
    bmp: "image/bmp",
    svg: "image/svg+xml",
    dwg: "application/acad",
    dxf: "application/dxf",
    rvt: "application/octet-stream",
    doc: "application/msword",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    xls: "application/vnd.ms-excel",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ppt: "application/vnd.ms-powerpoint",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    txt: "text/plain",
    csv: "text/csv",
    md: "text/markdown",
    json: "application/json",
    zip: "application/zip",
  };
  const ext = lower.split(".").pop() ?? "";
  return m[ext] ?? "application/octet-stream";
}

export async function extractZipForReview(
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<ExtractedZipEntry[]> {
  // Lazy-load JSZip so it doesn't bloat the initial bundle for users who
  // never touch the Documents page.
  const { default: JSZip } = await import("jszip");
  const zip = await JSZip.loadAsync(file);

  const candidates: { name: string; entry: import("jszip").JSZipObject }[] = [];
  zip.forEach((relPath, entry) => {
    if (entry.dir) return;
    const lower = relPath.toLowerCase();
    if (lower.includes("__macosx/") || lower.endsWith(".ds_store")) return;
    const basename = relPath.split("/").pop() ?? "";
    if (!basename || basename.startsWith("._")) return;
    candidates.push({ name: basename, entry });
  });

  const out: ExtractedZipEntry[] = [];
  for (let i = 0; i < candidates.length; i++) {
    const { name, entry } = candidates[i];
    const blob = await entry.async("blob");
    if (blob.size === 0) continue;
    const mime = _guessMime(name);
    out.push({
      filename: name,
      mime,
      blob,
      suggestedCategory: _suggestCategory(name, mime),
    });
    onProgress?.((i + 1) / candidates.length);
  }
  return out;
}

// Patch the category on an existing document — used by the ZIP review modal
// when the user corrects the heuristic suggestion.
export function updateDocumentCategory(
  id: string,
  category: DocumentCategory,
): Promise<DocumentRead> {
  return api<DocumentRead>(`/documents/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ category }),
  });
}

// Share/unshare a document or photo to the client's owner portal.
export function updateDocumentVisibility(
  id: string,
  client_visible: boolean,
): Promise<DocumentRead> {
  return api<DocumentRead>(`/documents/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ client_visible }),
  });
}

// Full upload: presign → PUT to R2 → finalize. Returns the stored document.
export async function uploadDocument(
  projectId: string,
  file: File,
  category: DocumentCategory,
  onProgress?: (fraction: number) => void,
  /** True when the person saw and accepted the category before uploading. */
  categoryConfirmed = false,
): Promise<DocumentRead> {
  const presigned = await api<PresignResponse>("/documents/presign", {
    method: "POST",
    body: JSON.stringify({
      project_id: projectId,
      filename: file.name,
      content_type: file.type || "application/octet-stream",
      category,
      category_confirmed: categoryConfirmed,
    }),
  });
  await uploadToR2(presigned.upload_url, file, onProgress);
  return api<DocumentRead>(`/documents/${presigned.document_id}/finalize`, {
    method: "POST",
    body: JSON.stringify({ size_bytes: file.size }),
  });
}
