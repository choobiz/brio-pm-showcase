"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Box, Label, Tag } from "@/components/kit";
import { WorkspaceError } from "@/components/WorkspaceError";
import {
  DOCUMENT_CATEGORIES,
  type DocumentCategory,
  type DocumentIndexBadge,
  type DocumentRead,
  bulkUpdateDocuments,
  confirmDocumentCategory,
  extractZipForReview,
  getDownloadUrl,
  getThumbnailUrl,
  getUsedIn,
  listDocuments,
  listIndexStatus,
  listTextStatus,
  readTextAgain,
  TEXT_UNREADABLE,
  type DocumentTextStatus,
  queueDocumentIndex,
  renameDocument,
  restoreDocument,
  setDocumentCategory,
  suggestCategories,
  updateDocumentVisibility,
  uploadDocument,
} from "@/lib/documents";
import {
  confirmDrawingRegister,
  getDrawingRegister,
  type DrawingRegister,
} from "@/lib/drawingRegister";
import { getProject, updateProject, type ProjectRead } from "@/lib/projects";
import { useVisiblePolling } from "@/lib/usePolling";
import { workspaceGet } from "@/lib/workspaceApi";
import { DocumentPreview } from "./DocumentPreview";

/**
 * Documents (wireframe round 1, Documents board; built 2026-10-12, phase 2).
 *
 * Folders down the left are the categories plus five states a file can be in:
 * needs a category, couldn't be read, shared with the client, made by the app,
 * archived. The list edits category in place, takes a selection for bulk
 * changes, and the panel on the right says what the AI read, which revision a
 * drawing is, and where the file is used.
 *
 * Every upload goes through the review: each file gets the server's suggested
 * category and nothing is stored until it is confirmed. A category chosen
 * there counts as confirmed; "Needs a category" is what nobody has confirmed
 * and is still "Other".
 *
 * "Ask the documents" searches names and AI summaries. Searching INSIDE the
 * files is phase 2b and needs them read first; the box says so.
 */

/** Rendered by the app (scope PDFs, invoices, the project index), not uploaded. */
const GENERATED = new Set(["scope_pdf", "project_index", "invoice_pdf", "change_order_pdf"]);

/** A project can hold 4,000 files. Rendering them all at once froze the tab. */
const PAGE = 200;

/** JSZip holds the whole archive in memory; past this, split the archive. */
const ZIP_MAX_BYTES = 50 * 1024 * 1024;

type Folder =
  | { kind: "all" }
  | { kind: "category"; value: string }
  | { kind: "needs_category" }
  | { kind: "unreadable" }
  | { kind: "shared" }
  | { kind: "generated" }
  | { kind: "archived" };

type Upload = { id: string; name: string; progress: number; error: string | null };

type ReviewRow = {
  filename: string;
  suggested: DocumentCategory;
  category: DocumentCategory;
  blob: Blob;
  mime: string;
};

type RevisionInfo = { label: string; current: boolean; proposed: boolean; currentLabel: string };

function fmtBytes(n: number | null): string {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" });
}

function catLabel(value: string): string {
  if (value === "scope_pdf") return "Scope PDF";
  if (value === "project_index") return "Project index";
  if (value === "invoice_pdf") return "Invoice PDF";
  if (value === "change_order_pdf") return "Change order PDF";
  return DOCUMENT_CATEGORIES.find((c) => c.value === value)?.label ?? value;
}

function kindOf(d: DocumentRead): string {
  const ext = (d.filename.split(".").pop() ?? "").toLowerCase();
  if ((d.mime ?? "").includes("pdf") || ext === "pdf") return "PDF";
  if (ext === "dwg" || ext === "dxf") return "CAD";
  if ((d.mime ?? "").startsWith("image/")) return "Image";
  if ((d.mime ?? "").startsWith("video/")) return "Video";
  return ext.toUpperCase().slice(0, 4) || "File";
}

const needsCategory = (d: DocumentRead) => d.category === "other" && !d.category_confirmed_at;

function newId(name: string) {
  return typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${name}`;
}

/** Each download through a hidden frame: the URLs are attachment-disposition,
 *  so the page stays put, and the browser does not block the second one as a
 *  popup the way it does with window.open in a loop. */
function downloadVia(url: string) {
  const f = document.createElement("iframe");
  f.style.display = "none";
  f.src = url;
  document.body.appendChild(f);
  setTimeout(() => f.remove(), 60_000);
}

export default function DocumentsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: projectId } = use(params);

  const [docs, setDocs] = useState<DocumentRead[] | null>(null);
  const [archivedDocs, setArchivedDocs] = useState<DocumentRead[] | null>(null);
  const [badges, setBadges] = useState<Record<string, DocumentIndexBadge>>({});
  const [texts, setTexts] = useState<Record<string, DocumentTextStatus>>({});
  const [usedIn, setUsedIn] = useState<Record<string, string[]>>({});
  const [register, setRegister] = useState<DrawingRegister | null>(null);
  const [project, setProject] = useState<ProjectRead | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);

  const [folder, setFolder] = useState<Folder>({ kind: "all" });
  const [view, setView] = useState<"files" | "sets">("files");
  const [filter, setFilter] = useState("");
  const [shown, setShown] = useState(PAGE);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [previewDoc, setPreviewDoc] = useState<DocumentRead | null>(null);
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const thumbAsked = useRef(new Set<string>());

  const [uploads, setUploads] = useState<Upload[]>([]);
  const [review, setReview] = useState<ReviewRow[] | null>(null);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  // ── loading ────────────────────────────────────────────────────────────
  const loadDocs = useCallback(async () => {
    setLoadError(null);
    try {
      setDocs(await listDocuments(projectId));
    } catch (e) {
      setLoadError(e);
    }
  }, [projectId]);

  const loadBadges = useCallback(async () => {
    try {
      const rows = await listIndexStatus(projectId);
      setBadges(Object.fromEntries(rows.map((b) => [b.document_id, b])));
    } catch {
      /* the list still works without the AI column */
    }
    try {
      const rows = await listTextStatus(projectId);
      setTexts(Object.fromEntries(rows.map((t) => [t.document_id, t])));
    } catch {
      /* nor does it need the search status */
    }
  }, [projectId]);

  const loadSide = useCallback(() => {
    getUsedIn(projectId).then(setUsedIn).catch(() => {});
    getDrawingRegister(projectId).then(setRegister).catch(() => {});
  }, [projectId]);

  useEffect(() => {
    loadDocs();
    loadBadges();
    loadSide();
    getProject(projectId).then(setProject).catch(() => {});
  }, [projectId, loadDocs, loadBadges, loadSide]);

  const reading =
    Object.values(badges).some((b) => b.status === "queued" || b.status === "running") ||
    Object.values(texts).some((t) => t.status === "pending" || t.status === "running");
  useVisiblePolling(loadBadges, 8000, reading);

  const loadArchived = useCallback(() => {
    listDocuments(projectId, true).then(setArchivedDocs).catch(setActionError);
  }, [projectId]);
  useEffect(() => {
    if (folder.kind === "archived" && archivedDocs === null) loadArchived();
  }, [folder, archivedDocs, loadArchived]);

  // ── derived ────────────────────────────────────────────────────────────
  const revisions = useMemo(() => {
    const m: Record<string, RevisionInfo> = {};
    for (const s of register?.sheets ?? []) {
      const cur = s.revisions.find((r) => r.is_current) ?? s.revisions[0];
      for (const r of s.revisions) {
        m[r.id] = {
          label: r.revision ?? (s.revision_count > 1 ? "unlabelled" : "—"),
          current: r.is_current,
          proposed: s.status === "proposed",
          currentLabel: cur?.revision ?? cur?.filename ?? "",
        };
      }
    }
    return m;
  }, [register]);

  // Couldn't be read: the AI summary failed, or the text could not be read for
  // searching (a scan with no text layer, too large, damaged).
  const unreadable = useCallback(
    (d: DocumentRead) => badges[d.id]?.status === "failed" || TEXT_UNREADABLE.has(texts[d.id]?.status ?? ""),
    [badges, texts],
  );

  const uploaded = useMemo(() => (docs ?? []).filter((d) => !GENERATED.has(d.category)), [docs]);
  const generated = useMemo(() => (docs ?? []).filter((d) => GENERATED.has(d.category)), [docs]);

  const counts = useMemo(() => {
    const byCat: Record<string, number> = {};
    for (const d of uploaded) byCat[d.category] = (byCat[d.category] ?? 0) + 1;
    return {
      byCat,
      needs: uploaded.filter(needsCategory).length,
      unreadable: uploaded.filter(unreadable).length,
      shared: (docs ?? []).filter((d) => d.client_visible).length,
    };
  }, [uploaded, docs, unreadable]);

  const inFolder = useMemo(() => {
    switch (folder.kind) {
      case "all":
        return uploaded;
      case "category":
        return uploaded.filter((d) => d.category === folder.value);
      case "needs_category":
        return uploaded.filter(needsCategory);
      case "unreadable":
        return uploaded.filter(unreadable);
      case "shared":
        return (docs ?? []).filter((d) => d.client_visible);
      case "generated":
        return generated;
      case "archived":
        return archivedDocs ?? [];
    }
  }, [folder, uploaded, generated, docs, unreadable, archivedDocs]);

  const visible = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return inFolder
      .filter((d) => !q || d.filename.toLowerCase().includes(q))
      .sort((a, b) => a.filename.localeCompare(b.filename));
  }, [inFolder, filter]);
  const rendered = visible.slice(0, shown);

  useEffect(() => {
    setShown(PAGE);
    setChecked(new Set());
  }, [folder, filter]);

  // Thumbnails only for the rows on screen: the old page asked for one per
  // photo on load, 3,401 requests on 357 East 4th.
  useEffect(() => {
    const want = rendered.filter(
      (d) => (d.mime ?? "").startsWith("image/") && !thumbAsked.current.has(d.id),
    );
    for (const d of want) {
      thumbAsked.current.add(d.id);
      getThumbnailUrl(d.id)
        .then(({ thumbnail_url }) => setThumbs((t) => ({ ...t, [d.id]: thumbnail_url })))
        .catch(() => {});
    }
  }, [rendered]);

  const selected = (folder.kind === "archived" ? archivedDocs : docs)?.find((d) => d.id === selectedId) ?? null;
  const selectedIndex = visible.findIndex((d) => d.id === selectedId);

  // ── keyboard: ↑↓ walk, space previews, as before ───────────────────────
  const move = useCallback(
    (delta: number) => {
      if (!visible.length) return;
      const next = Math.min(visible.length - 1, Math.max(0, (selectedIndex < 0 ? -1 : selectedIndex) + delta));
      const d = visible[next];
      if (next >= shown) setShown(next + 1);
      setSelectedId(d.id);
      setPreviewDoc((cur) => (cur ? d : cur));
      document.querySelector(`[data-doc-row="${d.id}"]`)?.scrollIntoView({ block: "nearest" });
    },
    [visible, selectedIndex, shown],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(el.tagName) || el.isContentEditable)) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        move(1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        move(-1);
      } else if (e.key === " ") {
        e.preventDefault();
        const d = visible[selectedIndex] ?? visible[0];
        if (!d) return;
        setSelectedId(d.id);
        setPreviewDoc((cur) => (cur ? null : d));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move, visible, selectedIndex]);

  // ── edits ──────────────────────────────────────────────────────────────
  const replace = (d: DocumentRead) => {
    setDocs((all) => (all ? all.map((x) => (x.id === d.id ? d : x)) : all));
  };

  const run = async (fn: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(e);
    }
  };

  const changeCategory = (d: DocumentRead, category: string) =>
    run(async () => replace(await setDocumentCategory(d.id, category)));

  const confirmCategory = (d: DocumentRead) => run(async () => replace(await confirmDocumentCategory(d.id)));

  const rename = (d: DocumentRead, name: string) => run(async () => replace(await renameDocument(d.id, name)));

  const toggleShare = (d: DocumentRead) =>
    run(async () => replace(await updateDocumentVisibility(d.id, !d.client_visible)));

  const archive = (d: DocumentRead) =>
    run(async () => {
      if (!confirm(`Archive "${d.filename}"? It leaves the list and can be restored from Archived.`)) return;
      await bulkUpdateDocuments(projectId, [d.id], { archived: true });
      setDocs((all) => all?.filter((x) => x.id !== d.id) ?? all);
      setArchivedDocs(null);
      setSelectedId(null);
    });

  const restore = (d: DocumentRead) =>
    run(async () => {
      const back = await restoreDocument(d.id);
      setArchivedDocs((all) => all?.filter((x) => x.id !== d.id) ?? all);
      setDocs((all) => (all ? [back, ...all] : all));
    });

  const download = (d: DocumentRead) =>
    run(async () => {
      const { download_url } = await getDownloadUrl(d.id);
      downloadVia(download_url);
    });

  const readText = (d: DocumentRead) =>
    run(async () => {
      await readTextAgain(d.id);
      setTexts((m) => ({
        ...m,
        [d.id]: { ...(m[d.id] ?? { page_count: null, pages_with_text: null, truncated: false }), document_id: d.id, status: "pending", error: null },
      }));
    });

  const readWithAi = (d: DocumentRead) =>
    run(async () => {
      const b = await queueDocumentIndex(d.id, true);
      setBadges((m) => ({ ...m, [d.id]: b }));
    });

  const bulk = (change: { category?: string; client_visible?: boolean; archived?: boolean }) =>
    run(async () => {
      const ids = [...checked];
      if (change.archived === true && !confirm(`Archive ${ids.length} files? They can be restored from Archived.`))
        return;
      await bulkUpdateDocuments(projectId, ids, change);
      setChecked(new Set());
      setArchivedDocs(null);
      await loadDocs();
      if (folder.kind === "archived") loadArchived();
    });

  const bulkDownload = () =>
    run(async () => {
      for (const id of checked) {
        const { download_url } = await getDownloadUrl(id);
        downloadVia(download_url);
      }
    });

  // ── upload: every file goes through the review ──────────────────────────
  const addToReview = async (files: { filename: string; blob: Blob; mime: string; guess: DocumentCategory }[]) => {
    let server: Record<string, string> = {};
    try {
      server = await suggestCategories(files.map((f) => f.filename));
    } catch {
      /* fall back to the browser's guess; the person still confirms each one */
    }
    const known = new Set(DOCUMENT_CATEGORIES.map((c) => c.value as string));
    const rows: ReviewRow[] = files.map((f) => {
      const s = server[f.filename];
      const cat = (s && known.has(s) ? s : f.guess) as DocumentCategory;
      return { filename: f.filename, suggested: cat, category: cat, blob: f.blob, mime: f.mime };
    });
    setReview((prev) => (prev ? [...prev, ...rows] : rows));
  };

  const takeFiles = (list: FileList | File[]) => {
    const plain: { filename: string; blob: Blob; mime: string; guess: DocumentCategory }[] = [];
    for (const file of Array.from(list)) {
      const isZip = file.name.toLowerCase().endsWith(".zip") || file.type === "application/zip";
      if (!isZip) {
        plain.push({
          filename: file.name,
          blob: file,
          mime: file.type || "application/octet-stream",
          guess: file.type.startsWith("image/") ? "photo" : "other",
        });
        continue;
      }
      const uid = newId(file.name);
      if (file.size > ZIP_MAX_BYTES) {
        setUploads((u) => [
          ...u,
          { id: uid, name: file.name, progress: 0, error: `Over ${fmtBytes(ZIP_MAX_BYTES)}. Split the archive and try again.` },
        ]);
        continue;
      }
      setUploads((u) => [...u, { id: uid, name: `Opening ${file.name}`, progress: 0, error: null }]);
      extractZipForReview(file, (p) => setUploads((u) => u.map((x) => (x.id === uid ? { ...x, progress: p } : x))))
        .then((entries) => {
          setUploads((u) => u.filter((x) => x.id !== uid));
          if (!entries.length) {
            setActionError(new Error(`No usable files inside "${file.name}".`));
            return;
          }
          addToReview(entries.map((e) => ({ filename: e.filename, blob: e.blob, mime: e.mime, guess: e.suggestedCategory })));
        })
        .catch((e) =>
          setUploads((u) => u.map((x) => (x.id === uid ? { ...x, error: String(e?.message ?? e) } : x))),
        );
    }
    if (plain.length) addToReview(plain);
  };

  const saveReview = async () => {
    if (!review) return;
    setReviewSaving(true);
    const rows = review;
    setReview(null);
    // Six at a time: a 200-file archive all at once starved the browser's
    // connection pool and the progress bars stalled together.
    const queue = [...rows];
    const worker = async () => {
      for (let row = queue.shift(); row; row = queue.shift()) {
        const uid = newId(row.filename);
        setUploads((u) => [...u, { id: uid, name: row!.filename, progress: 0, error: null }]);
        try {
          await uploadDocument(
            projectId,
            new File([row.blob], row.filename, { type: row.mime }),
            row.category,
            (p) => setUploads((u) => u.map((x) => (x.id === uid ? { ...x, progress: p } : x))),
            true,
          );
          setUploads((u) => u.filter((x) => x.id !== uid));
        } catch (e) {
          setUploads((u) =>
            u.map((x) => (x.id === uid ? { ...x, error: String((e as Error)?.message ?? e) } : x)),
          );
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(6, rows.length) }, worker));
    setReviewSaving(false);
    await loadDocs();
    loadSide();
    loadBadges();
  };

  // ── render ─────────────────────────────────────────────────────────────
  if (loadError) return <WorkspaceError error={loadError} onRetry={loadDocs} />;
  if (!docs) return <p className="text-sm text-slate-500">Loading documents…</p>;

  const allChecked = rendered.length > 0 && rendered.every((d) => checked.has(d.id));
  const archivedView = folder.kind === "archived";

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            if (e.dataTransfer.files.length) takeFiles(e.dataTransfer.files);
          }}
          className={`flex flex-wrap items-center justify-between gap-3 rounded-lg border-2 border-dashed p-4 ${
            dragOver ? "border-blue-600 bg-blue-50" : "border-slate-300 bg-white"
          }`}
        >
          <div className="min-w-0">
            <div className="text-sm font-semibold text-slate-900">Drop files or a .zip here</div>
            <p className="m-0 mt-0.5 max-w-xl text-[13px] text-slate-600">
              Each file gets a suggested category. You confirm before anything is saved. A new drawing revision is
              matched to its sheet under Drawing sets.
            </p>
          </div>
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            className="rounded-md border border-slate-400 bg-white px-3 py-1.5 text-sm text-slate-800 hover:border-slate-700"
          >
            Browse…
          </button>
          <input
            ref={fileInput}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) takeFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
        <AskBox projectId={projectId} onOpen={(id) => setSelectedId(id)} />
      </div>

      {uploads.length > 0 && <UploadList uploads={uploads} onClear={() => setUploads((u) => u.filter((x) => !x.error))} />}
      {actionError != null && <WorkspaceError error={actionError} />}

      <div className="grid gap-4 lg:grid-cols-[200px_minmax(0,1fr)]">
        <Folders
          folder={folder}
          onPick={(f) => {
            setFolder(f);
            setView("files");
          }}
          total={uploaded.length}
          counts={counts}
          generated={generated.length}
          archived={archivedDocs?.length ?? null}
          project={project}
          onSaveDrive={async (url) => {
            const p = await updateProject(projectId, { google_drive_url: url });
            setProject(p);
          }}
        />

        <div className="min-w-0 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-md border border-slate-300 bg-white p-0.5 text-sm">
              {(["files", "sets"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  onClick={() => setView(v)}
                  className={`rounded px-3 py-1 ${view === v ? "bg-slate-900 text-white" : "text-slate-700"}`}
                >
                  {v === "files" ? "Files" : "Drawing sets"}
                </button>
              ))}
            </div>
            {view === "files" && (
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter by name"
                className="w-56 rounded-md border border-slate-300 px-2 py-1 text-sm"
              />
            )}
            <span className="text-xs text-slate-500">
              {view === "files" && `${visible.length.toLocaleString()} files · ↑↓ to move · space to preview`}
            </span>
          </div>

          {view === "sets" ? (
            <DrawingSets
              register={register}
              onConfirm={async () => {
                if (!register) return;
                await run(async () => {
                  await confirmDrawingRegister(projectId, register.fingerprint);
                  setRegister(await getDrawingRegister(projectId));
                });
              }}
              onOpen={(id) => {
                setView("files");
                setFolder({ kind: "all" });
                setSelectedId(id);
              }}
            />
          ) : (
            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
              <div className="min-w-0 rounded-lg border border-slate-300 bg-white">
                {checked.size > 0 && (
                  <BulkBar
                    count={checked.size}
                    archivedView={archivedView}
                    onCategory={(c) => bulk({ category: c })}
                    onShare={(v) => bulk({ client_visible: v })}
                    onDownload={bulkDownload}
                    onArchive={(v) => bulk({ archived: v })}
                    onClear={() => setChecked(new Set())}
                  />
                )}
                {archivedView && archivedDocs === null ? (
                  <p className="m-0 p-4 text-sm text-slate-500">Loading archived files…</p>
                ) : visible.length === 0 ? (
                  <p className="m-0 p-4 text-sm text-slate-600">
                    {docs.length === 0 ? "No files yet. Drop some above." : "Nothing in this folder."}
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[760px] table-fixed text-sm">
                      <colgroup>
                        <col className="w-9" />
                        <col />
                        <col className="w-40" />
                        <col className="w-32" />
                        <col className="w-28" />
                        <col className="w-28" />
                        <col className="w-20" />
                      </colgroup>
                      <thead className="text-left text-xs text-slate-500">
                        <tr className="border-b border-slate-200">
                          <th className="px-2 py-2">
                            <input
                              type="checkbox"
                              aria-label="Select all shown"
                              checked={allChecked}
                              onChange={() =>
                                setChecked(allChecked ? new Set() : new Set(rendered.map((d) => d.id)))
                              }
                            />
                          </th>
                          <th className="py-2 pr-3 font-semibold">Name</th>
                          <th className="py-2 pr-3 font-semibold">Category</th>
                          <th className="py-2 pr-3 font-semibold">Revision</th>
                          <th className="py-2 pr-3 font-semibold">Read by AI</th>
                          <th className="py-2 pr-3 font-semibold">Used in</th>
                          <th className="py-2 pr-3 font-semibold">Client</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rendered.map((d) => (
                          <Row
                            key={d.id}
                            d={d}
                            selected={d.id === selectedId}
                            checked={checked.has(d.id)}
                            thumb={thumbs[d.id]}
                            badge={badges[d.id]}
                            rev={revisions[d.id]}
                            used={usedIn[d.id]}
                            archivedView={archivedView}
                            onSelect={() => setSelectedId(d.id)}
                            onPreview={() => {
                              setSelectedId(d.id);
                              setPreviewDoc(d);
                            }}
                            onCheck={() =>
                              setChecked((s) => {
                                const n = new Set(s);
                                if (n.has(d.id)) n.delete(d.id);
                                else n.add(d.id);
                                return n;
                              })
                            }
                            onCategory={(c) => changeCategory(d, c)}
                          />
                        ))}
                      </tbody>
                    </table>
                    {visible.length > shown && (
                      <div className="border-t border-slate-200 p-3 text-center">
                        <button
                          type="button"
                          onClick={() => setShown((n) => n + PAGE)}
                          className="text-sm text-blue-700 hover:text-blue-900"
                        >
                          Show {Math.min(PAGE, visible.length - shown)} more of {(visible.length - shown).toLocaleString()}
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              <DetailPanel
                d={selected}
                badge={selected ? badges[selected.id] : undefined}
                text={selected ? texts[selected.id] : undefined}
                rev={selected ? revisions[selected.id] : undefined}
                sheet={selected ? register?.sheets.find((s) => s.revisions.some((r) => r.id === selected.id)) : undefined}
                used={selected ? usedIn[selected.id] : undefined}
                projectId={projectId}
                onPreview={() => selected && setPreviewDoc(selected)}
                onDownload={() => selected && download(selected)}
                onRename={(name) => selected && rename(selected, name)}
                onCategory={(c) => selected && changeCategory(selected, c)}
                onConfirmCategory={() => selected && confirmCategory(selected)}
                onShare={() => selected && toggleShare(selected)}
                onArchive={() => selected && archive(selected)}
                onRestore={() => selected && restore(selected)}
                onRead={() => selected && readWithAi(selected)}
                onReadText={() => selected && readText(selected)}
                onNewRevision={(file) =>
                  addToReview([{ filename: file.name, blob: file, mime: file.type || "application/octet-stream", guess: "drawing" }])
                }
              />
            </div>
          )}
        </div>
      </div>

      {review && (
        <ReviewModal
          rows={review}
          saving={reviewSaving}
          onChange={setReview}
          onCancel={() => setReview(null)}
          onSave={saveReview}
        />
      )}

      {previewDoc && (
        <DocumentPreview
          documentId={previewDoc.id}
          filename={previewDoc.filename}
          position={selectedIndex >= 0 ? selectedIndex + 1 : null}
          total={visible.length}
          onPrev={() => move(-1)}
          onNext={() => move(1)}
          onClose={() => setPreviewDoc(null)}
          onDownload={() => download(previewDoc)}
        />
      )}
    </div>
  );
}

// ── pieces ────────────────────────────────────────────────────────────────

type Coverage = { readable: number; searchable: number; waiting: number; unreadable: number; not_queued: number };

/** Search results mark matched words with ⟦ ⟧; show them in bold. */
function Marked({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\u27e6[^\u27e7]*\u27e7)/).map((part, i) =>
        part.startsWith("\u27e6") ? <b key={i} className="font-semibold text-slate-900">{part.slice(1, -1)}</b> : part,
      )}
    </>
  );
}

function AskBox({ projectId, onOpen }: { projectId: string; onOpen: (id: string) => void }) {
  type Hit = { id: string; filename: string; matched_on: string; snippet: string | null; page?: number | null };
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const ask = async () => {
    if (!q.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await workspaceGet<{ results: Hit[]; text_coverage?: Coverage }>(
        `/documents/search?project_id=${encodeURIComponent(projectId)}&q=${encodeURIComponent(q.trim())}&limit=12`,
      );
      setHits(r.results);
      setCoverage(r.text_coverage ?? null);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const pendingRead = coverage ? coverage.waiting + coverage.not_queued : 0;

  return (
    <Box className="p-4">
      <Label className="mb-2">Ask the documents</Label>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          ask();
        }}
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder='e.g. bearing capacity, "anchor bolt", window -skylight'
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
        >
          {busy ? "…" : "Ask"}
        </button>
      </form>
      <p className="m-0 mt-1.5 text-xs text-slate-500">
        Searches file names, what the AI wrote about each file, and the words inside PDFs, Word, Excel and text files.
        {coverage &&
          ` Inside ${coverage.searchable.toLocaleString()} of ${coverage.readable.toLocaleString()} such files` +
            (pendingRead ? `; ${pendingRead.toLocaleString()} not read yet` : "") +
            (coverage.unreadable ? `; ${coverage.unreadable.toLocaleString()} have no readable text (see Couldn't be read)` : "") +
            "."}
      </p>
      {err != null && <WorkspaceError error={err} className="mt-2" />}
      {hits && (
        <ul className="m-0 mt-2 list-none space-y-2 p-0">
          {hits.length === 0 && <li className="text-sm text-slate-600">Nothing matches.</li>}
          {hits.map((h) => (
            <li key={h.id} className="text-sm">
              <button type="button" onClick={() => onOpen(h.id)} className="text-left text-blue-700 hover:text-blue-900">
                {h.filename}
              </button>
              <span className="text-xs text-slate-500"> · {h.matched_on}</span>
              {h.snippet && (
                <div className="text-xs text-slate-600">
                  …<Marked text={h.snippet} />…
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Box>
  );
}

function UploadList({ uploads, onClear }: { uploads: Upload[]; onClear: () => void }) {
  const failed = uploads.filter((u) => u.error).length;
  return (
    <Box className="space-y-2 p-4">
      <div className="flex items-center justify-between">
        <Label>Uploading · {uploads.length - failed}</Label>
        {failed > 0 && (
          <button type="button" onClick={onClear} className="text-xs text-blue-700">
            Clear {failed} failed
          </button>
        )}
      </div>
      <ul className="m-0 max-h-48 list-none space-y-1.5 overflow-y-auto p-0">
        {uploads.map((u) => (
          <li key={u.id} className="text-[13px]">
            <div className="flex justify-between gap-3 text-slate-700">
              <span className="truncate">{u.name}</span>
              <span className={u.error ? "text-red-700" : ""}>{u.error ? "failed" : `${Math.round(u.progress * 100)}%`}</span>
            </div>
            <div className="mt-0.5 h-1 w-full bg-slate-200">
              <div
                className={`h-1 ${u.error ? "bg-red-500" : "bg-slate-900"}`}
                style={{ width: `${u.error ? 100 : u.progress * 100}%` }}
              />
            </div>
            {u.error && <div className="text-xs text-red-700">{u.error}</div>}
          </li>
        ))}
      </ul>
    </Box>
  );
}

function Folders({
  folder,
  onPick,
  total,
  counts,
  generated,
  archived,
  project,
  onSaveDrive,
}: {
  folder: Folder;
  onPick: (f: Folder) => void;
  total: number;
  counts: { byCat: Record<string, number>; needs: number; unreadable: number; shared: number };
  generated: number;
  archived: number | null;
  project: ProjectRead | null;
  onSaveDrive: (url: string | null) => Promise<void>;
}) {
  const same = (f: Folder) =>
    f.kind === folder.kind && (f.kind !== "category" || (folder.kind === "category" && f.value === folder.value));
  const item = (f: Folder, label: string, n: number | null, warn = false) => (
    <li key={label}>
      <button
        type="button"
        onClick={() => onPick(f)}
        className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-sm ${
          same(f) ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"
        }`}
      >
        <span className="truncate">{label}</span>
        {n != null && (
          <span className={`text-xs ${same(f) ? "text-slate-200" : warn && n > 0 ? "font-semibold text-orange-800" : "text-slate-500"}`}>
            {n.toLocaleString()}
          </span>
        )}
      </button>
    </li>
  );

  return (
    <nav aria-label="Folders" className="space-y-3">
      <ul className="m-0 list-none space-y-0.5 p-0">
        {item({ kind: "all" }, "All files", total)}
        {DOCUMENT_CATEGORIES.filter((c) => counts.byCat[c.value]).map((c) =>
          item({ kind: "category", value: c.value }, c.label, counts.byCat[c.value]),
        )}
      </ul>
      <ul className="m-0 list-none space-y-0.5 border-t border-slate-300 p-0 pt-3">
        {item({ kind: "needs_category" }, "Needs a category", counts.needs, true)}
        {item({ kind: "unreadable" }, "Couldn't be read", counts.unreadable, true)}
        {item({ kind: "shared" }, "Shared with client", counts.shared)}
        {generated > 0 && item({ kind: "generated" }, "Made by the app", generated)}
        {item({ kind: "archived" }, "Archived", archived)}
      </ul>
      <DriveLink project={project} onSave={onSaveDrive} />
    </nav>
  );
}

function DriveLink({ project, onSave }: { project: ProjectRead | null; onSave: (url: string | null) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState<unknown>(null);
  if (!project) return null;
  const url = project.google_drive_url;
  return (
    <div className="border-t border-slate-300 pt-3 text-[13px]">
      <div className="text-xs text-slate-500">Source folder on Google Drive</div>
      {editing ? (
        <form
          className="mt-1 space-y-1.5"
          onSubmit={async (e) => {
            e.preventDefault();
            setErr(null);
            try {
              await onSave(draft.trim() || null);
              setEditing(false);
            } catch (x) {
              setErr(x);
            }
          }}
        >
          <input
            type="url"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="https://drive.google.com/…"
            autoFocus
            className="w-full rounded border border-slate-300 px-2 py-1 text-[13px]"
          />
          <div className="flex gap-2">
            <button type="submit" className="text-blue-700">
              Save
            </button>
            <button type="button" onClick={() => setEditing(false)} className="text-slate-600">
              Cancel
            </button>
          </div>
          {err != null && <WorkspaceError error={err} />}
        </form>
      ) : (
        <div className="mt-0.5 flex flex-wrap gap-x-3">
          {url ? (
            <a href={url} target="_blank" rel="noopener noreferrer" className="text-blue-700">
              Open in Drive ↗
            </a>
          ) : (
            <span className="text-slate-500">Not set</span>
          )}
          <button
            type="button"
            onClick={() => {
              setDraft(url ?? "");
              setEditing(true);
            }}
            className="text-blue-700"
          >
            {url ? "Change" : "Add"}
          </button>
        </div>
      )}
    </div>
  );
}

function BulkBar({
  count,
  archivedView,
  onCategory,
  onShare,
  onDownload,
  onArchive,
  onClear,
}: {
  count: number;
  archivedView: boolean;
  onCategory: (c: string) => void;
  onShare: (v: boolean) => void;
  onDownload: () => void;
  onArchive: (v: boolean) => void;
  onClear: () => void;
}) {
  const btn = "rounded-md border border-slate-400 bg-white px-2.5 py-1 text-[13px] text-slate-800 hover:border-slate-700";
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-slate-300 bg-slate-50 px-3 py-2">
      <b className="text-sm text-slate-900">{count} selected</b>
      {archivedView ? (
        <button type="button" className={btn} onClick={() => onArchive(false)}>
          Restore
        </button>
      ) : (
        <>
          <select
            aria-label="Change category"
            value=""
            onChange={(e) => e.target.value && onCategory(e.target.value)}
            className="rounded-md border border-slate-400 bg-white px-2 py-1 text-[13px]"
          >
            <option value="">Change category…</option>
            {DOCUMENT_CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
          <button type="button" className={btn} onClick={() => onShare(true)}>
            Share with client
          </button>
          <button type="button" className={btn} onClick={() => onShare(false)}>
            Stop sharing
          </button>
          <button type="button" className={btn} onClick={onDownload}>
            Download
          </button>
          <button type="button" className={btn} onClick={() => onArchive(true)}>
            Archive
          </button>
        </>
      )}
      <button type="button" onClick={onClear} className="ml-auto text-[13px] text-blue-700">
        Clear
      </button>
    </div>
  );
}

function Row({
  d,
  selected,
  checked,
  thumb,
  badge,
  rev,
  used,
  archivedView,
  onSelect,
  onPreview,
  onCheck,
  onCategory,
}: {
  d: DocumentRead;
  selected: boolean;
  checked: boolean;
  thumb?: string;
  badge?: DocumentIndexBadge;
  rev?: RevisionInfo;
  used?: string[];
  archivedView: boolean;
  onSelect: () => void;
  onPreview: () => void;
  onCheck: () => void;
  onCategory: (c: string) => void;
}) {
  const unconfirmedOther = needsCategory(d);
  return (
    <tr
      data-doc-row={d.id}
      onClick={onSelect}
      onDoubleClick={onPreview}
      className={`cursor-pointer border-b border-slate-100 align-top ${selected ? "bg-slate-100" : "hover:bg-slate-50"}`}
    >
      <td className="px-2 py-2" onClick={(e) => e.stopPropagation()}>
        <input type="checkbox" aria-label={`Select ${d.filename}`} checked={checked} onChange={onCheck} />
      </td>
      <td className="py-2 pr-3">
        <div className="flex min-w-0 items-start gap-2">
          {thumb ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={thumb} alt="" className="h-8 w-8 shrink-0 object-cover" />
          ) : (
            <span className="flex h-8 w-8 shrink-0 items-center justify-center bg-slate-100 text-[9px] font-semibold text-slate-500">
              {kindOf(d)}
            </span>
          )}
          <div className="min-w-0">
            <div className="truncate font-medium text-slate-900" title={d.filename}>
              {d.filename}
            </div>
            <div className="text-xs text-slate-500">
              {kindOf(d)} · {fmtBytes(d.size_bytes)} · {fmtDate(d.created_at)}
            </div>
          </div>
        </div>
      </td>
      <td className="py-2 pr-3" onClick={(e) => e.stopPropagation()}>
        {archivedView || GENERATED.has(d.category) ? (
          <span className="text-[13px] text-slate-600">{catLabel(d.category)}</span>
        ) : (
          <select
            aria-label={`Category of ${d.filename}`}
            value={unconfirmedOther ? "" : d.category}
            onChange={(e) => e.target.value && onCategory(e.target.value)}
            className={`w-full rounded border px-1.5 py-1 text-[13px] ${
              unconfirmedOther ? "border-orange-700 text-orange-800" : "border-slate-300 text-slate-800"
            }`}
          >
            {unconfirmedOther && <option value="">Pick a category</option>}
            {DOCUMENT_CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        )}
      </td>
      <td className="py-2 pr-3 text-[13px] text-slate-700">
        {rev ? (
          rev.current ? (
            <>
              {rev.label}
              {rev.label !== "—" && " · current"}
              {rev.proposed && <div className="text-xs text-slate-500">not confirmed</div>}
            </>
          ) : (
            <span className="text-slate-500">
              Replaced by {rev.currentLabel}
              {rev.proposed && <div className="text-xs">not confirmed</div>}
            </span>
          )
        ) : (
          <span className="text-slate-400">—</span>
        )}
      </td>
      <td className="py-2 pr-3 text-[13px]">
        <AiState badge={badge} />
      </td>
      <td className="py-2 pr-3 text-[13px] text-slate-700">{used?.length ? used.join(", ") : <span className="text-slate-400">—</span>}</td>
      <td className="py-2 pr-3 text-[13px]">{d.client_visible ? <Tag tone="ok">Shared</Tag> : <span className="text-slate-400">—</span>}</td>
    </tr>
  );
}

function AiState({ badge }: { badge?: DocumentIndexBadge }) {
  switch (badge?.status) {
    case "succeeded":
      return <span className="text-slate-800">Yes</span>;
    case "failed":
      return <span className="font-semibold text-orange-800">Couldn&apos;t read</span>;
    case "queued":
    case "running":
      return <span className="text-slate-600">Reading…</span>;
    case "skipped":
      return <span className="text-slate-500">Skipped</span>;
    default:
      return <span className="text-slate-400">Not yet</span>;
  }
}

function DetailPanel({
  d,
  badge,
  text,
  rev,
  sheet,
  used,
  projectId,
  onPreview,
  onDownload,
  onRename,
  onCategory,
  onConfirmCategory,
  onShare,
  onArchive,
  onRestore,
  onRead,
  onReadText,
  onNewRevision,
}: {
  d: DocumentRead | null;
  badge?: DocumentIndexBadge;
  text?: DocumentTextStatus;
  rev?: RevisionInfo;
  sheet?: DrawingRegister["sheets"][number];
  used?: string[];
  projectId: string;
  onPreview: () => void;
  onDownload: () => void;
  onRename: (name: string) => void;
  onCategory: (c: string) => void;
  onConfirmCategory: () => void;
  onShare: () => void;
  onArchive: () => void;
  onRestore: () => void;
  onRead: () => void;
  onReadText: () => void;
  onNewRevision: (file: File) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");
  const revInput = useRef<HTMLInputElement>(null);
  useEffect(() => setRenaming(false), [d?.id]);

  if (!d) {
    return (
      <Box className="h-fit p-4">
        <p className="m-0 text-sm text-slate-600">Select a file to see what the AI read, its revisions, and where it is used.</p>
      </Box>
    );
  }

  const linkFor: Record<string, string> = {
    Takeoff: `/projects/${projectId}/takeoff`,
    Scope: `/projects/${projectId}/scope`,
    Estimate: `/projects/${projectId}/estimate`,
    Costs: `/projects/${projectId}/workbench/cashflow`,
    Notes: `/projects/${projectId}/workbench/narratives`,
  };
  const btn = "rounded-md border border-slate-400 bg-white px-2.5 py-1 text-[13px] text-slate-800 hover:border-slate-700";

  return (
    <Box className="h-fit space-y-4 p-4 xl:sticky xl:top-4">
      <div>
        {renaming ? (
          <form
            className="space-y-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) onRename(name.trim());
              setRenaming(false);
            }}
          >
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              aria-label="New name"
              className="w-full rounded border border-slate-400 px-2 py-1 text-sm"
            />
            <div className="flex gap-3 text-[13px]">
              <button type="submit" className="text-blue-700">
                Save name
              </button>
              <button type="button" onClick={() => setRenaming(false)} className="text-slate-600">
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <div className="break-words text-sm font-semibold text-slate-900">
            {d.filename}{" "}
            {!d.archived && (
              <button
                type="button"
                onClick={() => {
                  setName(d.filename);
                  setRenaming(true);
                }}
                className="text-xs font-normal text-blue-700"
              >
                Rename
              </button>
            )}
          </div>
        )}
        <div className="mt-0.5 text-xs text-slate-500">
          {kindOf(d)} · {fmtBytes(d.size_bytes)} · added {fmtDate(d.created_at)}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {d.archived ? (
            <button type="button" className={btn} onClick={onRestore}>
              Restore
            </button>
          ) : (
            <>
              <button type="button" className={btn} onClick={onPreview}>
                Open
              </button>
              <button type="button" className={btn} onClick={onDownload}>
                Download
              </button>
              {d.category === "drawing" && (
                <>
                  <button type="button" className={btn} onClick={() => revInput.current?.click()}>
                    Upload new revision
                  </button>
                  <input
                    ref={revInput}
                    type="file"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) onNewRevision(f);
                      e.target.value = "";
                    }}
                  />
                </>
              )}
            </>
          )}
        </div>
      </div>

      {!d.archived && !GENERATED.has(d.category) && needsCategory(d) && (
        <div className="rounded-md border border-orange-700 p-3 text-[13px]">
          <div className="font-semibold text-orange-800">Needs a category</div>
          <p className="m-0 mt-0.5 text-slate-700">Filed as Other, and nobody has said that is right.</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <select
              aria-label="Category"
              value=""
              onChange={(e) => e.target.value && onCategory(e.target.value)}
              className="rounded border border-slate-400 px-1.5 py-1 text-[13px]"
            >
              <option value="">Pick a category…</option>
              {DOCUMENT_CATEGORIES.filter((c) => c.value !== "other").map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
            <button type="button" onClick={onConfirmCategory} className="text-blue-700">
              It is Other
            </button>
          </div>
        </div>
      )}

      <section>
        <Label className="mb-1.5">What the AI read</Label>
        {badge?.status === "succeeded" && badge.summary_text ? (
          <p className="m-0 whitespace-pre-line text-[13px] text-slate-700">{badge.summary_text}</p>
        ) : badge?.status === "failed" ? (
          <p className="m-0 text-[13px] text-slate-700">
            <b className="text-orange-800">Couldn&apos;t read this file.</b> {badge.error_message ?? "No reason was recorded."}
          </p>
        ) : badge?.status === "queued" || badge?.status === "running" ? (
          <p className="m-0 text-[13px] text-slate-600">Reading it now.</p>
        ) : (
          <p className="m-0 text-[13px] text-slate-600">Not read yet.</p>
        )}
        {!d.archived && badge?.status !== "queued" && badge?.status !== "running" && (
          <button type="button" onClick={onRead} className="mt-1 text-[13px] text-blue-700">
            {badge?.status === "succeeded" ? "Read it again" : badge?.status === "failed" ? "Try again" : "Read it with AI"}
          </button>
        )}
      </section>

      {text && (
        <section>
          <Label className="mb-1.5">Search inside</Label>
          {text.status === "ok" ? (
            <p className="m-0 text-[13px] text-slate-700">
              Searchable
              {text.page_count && text.page_count > 1
                ? ` · text on ${text.pages_with_text} of ${text.page_count} pages`
                : ""}
              {text.truncated ? " · only the first part was read" : ""}.
            </p>
          ) : text.status === "pending" || text.status === "running" ? (
            <p className="m-0 text-[13px] text-slate-600">Waiting to be read.</p>
          ) : (
            <p className="m-0 text-[13px] text-slate-700">
              <b className="text-orange-800">Not searchable.</b> {text.error ?? "No reason was recorded."}
            </p>
          )}
          {!d.archived && text.status !== "pending" && text.status !== "running" && text.status !== "ok" && (
            <button type="button" onClick={onReadText} className="mt-1 text-[13px] text-blue-700">
              Try again
            </button>
          )}
        </section>
      )}

      {sheet && (
        <section>
          <Label className="mb-1.5">Revisions</Label>
          <ul className="m-0 list-none space-y-1 p-0 text-[13px]">
            {sheet.revisions.map((r) => (
              <li key={r.id} className={r.id === d.id ? "font-semibold text-slate-900" : "text-slate-700"}>
                {r.revision ?? "No label"}
                {r.is_current && " · current"}
                {r.issued && ` · ${r.issued}`}
                {r.id !== d.id && <span className="block truncate text-xs font-normal text-slate-500">{r.filename}</span>}
              </li>
            ))}
          </ul>
          <p className="m-0 mt-1 text-xs text-slate-500">
            {sheet.status === "confirmed" ? "Grouping confirmed by a person." : `Proposed: ${sheet.grouped_because_plain}. Confirm it under Drawing sets.`}
          </p>
        </section>
      )}
      {!sheet && rev == null && d.category === "drawing" && (
        <p className="m-0 text-xs text-slate-500">Not in a drawing set yet.</p>
      )}

      <section>
        <Label className="mb-1.5">Used in</Label>
        {used?.length ? (
          <div className="flex flex-wrap gap-x-3 text-[13px]">
            {used.map((u) => (
              <Link key={u} href={linkFor[u] ?? "#"} className="text-blue-700">
                {u}
              </Link>
            ))}
          </div>
        ) : (
          <p className="m-0 text-[13px] text-slate-600">Not used anywhere yet.</p>
        )}
      </section>

      {!d.archived && (
        <section className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 pt-3">
          <label className="flex items-center gap-2 text-[13px] text-slate-800">
            <input type="checkbox" checked={d.client_visible} onChange={onShare} />
            Share with client portal
          </label>
          <button type="button" onClick={onArchive} className="text-[13px] text-red-700">
            Archive
          </button>
        </section>
      )}
    </Box>
  );
}

function DrawingSets({
  register,
  onConfirm,
  onOpen,
}: {
  register: DrawingRegister | null;
  onConfirm: () => void;
  onOpen: (id: string) => void;
}) {
  if (!register) return <Box className="p-4 text-sm text-slate-500">Loading drawing sets…</Box>;
  if (!register.sheets.length)
    return <Box className="p-4 text-sm text-slate-600">No drawings in this project yet.</Box>;
  const s = register.summary;
  return (
    <div className="space-y-3">
      <Box className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="text-[13px] text-slate-700">
          <b className="text-slate-900">{s.distinct_drawings}</b> drawings from {s.documents_in} files ·{" "}
          {s.drawings_with_more_than_one_revision} with more than one revision · {s.duplicates_found} duplicates
          <div className="text-xs text-slate-500">
            {s.confirmed_drawings} confirmed, {s.proposed_drawings} proposed from file names
          </div>
        </div>
        {s.proposed_drawings > 0 && (
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white"
            title="Records the grouping shown below. Refused if a drawing was added since this page loaded."
          >
            Confirm the {s.proposed_drawings} proposed
          </button>
        )}
      </Box>
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(280px,1fr))]">
        {register.sheets.map((sh) => (
          <Box key={sh.key} className="p-4">
            <div className="flex items-start justify-between gap-2">
              <b className="min-w-0 break-words text-[13px] text-slate-900">{sh.label}</b>
              <Tag tone={sh.status === "confirmed" ? "ok" : "plain"}>{sh.status === "confirmed" ? "confirmed" : "proposed"}</Tag>
            </div>
            <ul className="m-0 mt-2 list-none space-y-1 p-0 text-[13px]">
              {sh.revisions.map((r) => (
                <li key={r.id}>
                  <button type="button" onClick={() => onOpen(r.id)} className="text-left text-blue-700 hover:text-blue-900">
                    {r.revision ?? "No label"}
                    {r.is_current ? " · current" : ""}
                  </button>
                  <span className="block truncate text-xs text-slate-500">{r.filename}</span>
                </li>
              ))}
            </ul>
            {sh.status === "proposed" && <p className="m-0 mt-2 text-xs text-slate-500">{sh.grouped_because_plain}</p>}
          </Box>
        ))}
      </div>
      {register.duplicates.length > 0 && (
        <Box className="p-4">
          <Label className="mb-2">Duplicates · {register.duplicates.length}</Label>
          <ul className="m-0 list-none space-y-1 p-0 text-[13px] text-slate-700">
            {register.duplicates.map((x) => (
              <li key={x.id}>
                {x.filename} <span className="text-slate-500">is the same as {x.duplicate_of_filename} ({x.reason})</span>
              </li>
            ))}
          </ul>
        </Box>
      )}
    </div>
  );
}

function ReviewModal({
  rows,
  saving,
  onChange,
  onCancel,
  onSave,
}: {
  rows: ReviewRow[];
  saving: boolean;
  onChange: (rows: ReviewRow[]) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const changed = rows.filter((r) => r.category !== r.suggested).length;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => !saving && onCancel()}>
      <div
        role="dialog"
        aria-label="Check the categories"
        className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-slate-200 px-5 py-4">
          <h2 className="m-0 text-lg font-semibold text-slate-900">
            Check the categories · {rows.length} file{rows.length === 1 ? "" : "s"}
          </h2>
          <p className="m-0 mt-1 text-sm text-slate-600">
            Each category is a guess from the file name. Fix any that are wrong. Nothing is saved until you upload.
          </p>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-2">
          <table className="w-full text-sm">
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${i}-${r.filename}`} className="border-b border-slate-100">
                  <td className="break-all py-2 pr-3 text-slate-800">{r.filename}</td>
                  <td className="w-56 py-2">
                    <select
                      aria-label={`Category for ${r.filename}`}
                      value={r.category}
                      onChange={(e) =>
                        onChange(rows.map((x, j) => (j === i ? { ...x, category: e.target.value as DocumentCategory } : x)))
                      }
                      className={`w-full rounded border px-2 py-1 text-sm ${
                        r.category !== r.suggested ? "border-blue-600 bg-blue-50" : "border-slate-300"
                      }`}
                    >
                      {DOCUMENT_CATEGORIES.map((c) => (
                        <option key={c.value} value={c.value}>
                          {c.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="w-8 py-2 text-right">
                    <button
                      type="button"
                      aria-label={`Leave out ${r.filename}`}
                      title="Leave this file out"
                      onClick={() => onChange(rows.filter((_, j) => j !== i))}
                      className="text-slate-400 hover:text-red-700"
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-slate-200 bg-slate-50 px-5 py-3">
          <span className="text-xs text-slate-600">{changed} changed from the guess</span>
          <div className="flex gap-2">
            <button type="button" onClick={onCancel} disabled={saving} className="rounded-md px-3 py-1.5 text-sm text-slate-700">
              Cancel
            </button>
            <button
              type="button"
              onClick={onSave}
              disabled={saving || rows.length === 0}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            >
              Upload {rows.length} file{rows.length === 1 ? "" : "s"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
