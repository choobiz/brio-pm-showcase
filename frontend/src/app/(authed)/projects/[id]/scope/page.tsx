"use client";

import { errorMessage } from "@/lib/workspaceApi";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SkillFeedbackModal } from "@/components/SkillFeedbackModal";
import {
  createAIJob,
  streamAIJob,
  upTo,
  type AIJobImageInput,
  type AIJobStreamEvent,
} from "@/lib/aiJobs";
import { useJobCost } from "@/lib/useJobCost";
import {
  generateFullScopePdf,
  generateScopePdf,
  getScope,
  listScopes,
  updateScope,
  type ScopeCategory,
  type ScopeMaterial,
  type ScopePayload,
  type ScopeRead,
  type ScopeSource,
} from "@/lib/scopes";
import { listDocuments, type DocumentRead } from "@/lib/documents";
import Link from "next/link";
import { Box, Label } from "@/components/kit";
import { getInterview, type Interview } from "@/lib/workspaceInterview";
import DocumentSelector, { type SelectionSummary } from "./DocumentSelector";
import EditWithPrompt from "@/components/EditWithPrompt";

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

/**
 * Scope — what the job is, in words, versioned.
 *
 * Laid out per the round-1 wireframe: what it reads (top), versions and "what
 * should change?" (left), the current version (centre), and where it came
 * from (right). Describing a change saves a NEW version; the one it came from
 * stays as it was. Editing by hand still edits the version in place, with a
 * revision snapshot, as before.
 */
export default function ScopePage({ params }: { params: Promise<{ id: string }> }) {
  const { id: projectId } = use(params);
  const [scopes, setScopes] = useState<ScopeRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [showGenerate, setShowGenerate] = useState(false);
  const [showSelector, setShowSelector] = useState(false);
  const [comparing, setComparing] = useState<[string, string] | null>(null);
  const [pdfBusy, setPdfBusy] = useState<null | "version" | "whole">(null);
  const [interview, setInterview] = useState<Interview | null>(null);
  // The document selection lives on the PAGE, not in the generate dialog. It
  // is durable project state — it persists the moment it changes, and the CAD
  // takeoff and the readiness grade both read it on a later run. The selector
  // stays mounted (hidden) so the summary above is always current.
  const [selection, setSelection] = useState<SelectionSummary>({
    scopeIds: [], scopeCount: 0, quantityCount: 0, accuracyPct: 0, totalDocuments: 0,
  });
  const onSelectionChange = useCallback((s: SelectionSummary) => setSelection(s), []);

  const refresh = useCallback(async (select?: string) => {
    setLoading(true);
    try {
      const rows = await listScopes(projectId);
      setScopes(rows);
      if (select) setSelectedId(select);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    refresh();
    getInterview(projectId).then(setInterview).catch(() => setInterview(null));
  }, [refresh, projectId]);

  // listScopes is newest first.
  const latest = scopes[0] ?? null;
  const current = scopes.find((s) => s.id === selectedId) ?? latest;

  const pdf = async (which: "version" | "whole") => {
    if (!current) return;
    setPdfBusy(which);
    setError(null);
    try {
      const res = which === "version" ? await generateScopePdf(current.id) : await generateFullScopePdf(projectId);
      window.open(res.download_url, "_blank", "noopener");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPdfBusy(null);
    }
  };

  if (editingId) {
    return (
      <ScopeEditor
        projectId={projectId}
        scopeId={editingId}
        onBack={() => {
          setEditingId(null);
          refresh();
        }}
      />
    );
  }

  const questions = interview ? [...interview.blocking, ...interview.material] : [];

  return (
    <div className="space-y-4">
      <Box className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="min-w-0 flex-1">
          <div className="text-base font-semibold">
            Scope reads {selection.scopeCount} of {selection.totalDocuments} files
          </div>
          <p className="m-0 mt-0.5 text-[13px] text-slate-600">
            Picked under &ldquo;Change what it reads&rdquo;{selection.quantityCount ? `, plus ${selection.quantityCount} set aside for measuring` : ""}. A new
            version reads exactly these.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setShowSelector((v) => !v)}
            className="rounded-md border border-slate-900 px-3 py-1.5 text-sm"
          >
            {showSelector ? "Done choosing" : "Change what it reads"}
          </button>
          <button
            type="button"
            onClick={() => setShowGenerate(true)}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white"
          >
            Write a new version
          </button>
        </div>
        <div hidden={!showSelector} className="w-full border-t border-slate-200 pt-3">
          <p className="m-0 mb-2 text-xs text-slate-500">
            Two decisions per file: read it for the scope, or measure it for quantities. Saved as you tick.
          </p>
          <DocumentSelector projectId={projectId} onChange={onSelectionChange} />
        </div>
      </Box>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      {loading && !scopes.length ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : !current ? (
        <Box className="p-6 text-sm text-slate-600">
          No scope yet. Tick the files it should read, then <b>Write a new version</b>.
        </Box>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[230px_minmax(0,1fr)_260px]">
          {/* ── versions + what should change ─────────────────────── */}
          <Box className="h-fit space-y-2 p-3">
            <Label className="px-1">Versions</Label>
            {scopes.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => {
                  setSelectedId(s.id);
                  setComparing(null);
                }}
                className={`block w-full rounded-md p-2 text-left text-[13px] ${
                  s.id === current.id ? "border-2 border-slate-900" : "border border-slate-300"
                }`}
              >
                <b>
                  v{s.version}
                  {s.id === latest?.id ? " · current" : ""}
                </b>
                <div className="text-slate-600">{new Date(s.created_at).toLocaleDateString()}</div>
                {s.payload?._revision ? (
                  <div className="text-xs text-slate-500">
                    Changed from v{s.payload._revision.from_version}: {s.payload._revision.instruction}
                  </div>
                ) : s.source ? (
                  <div className="text-xs text-slate-500">
                    Written from {s.source.documents?.length ?? 0} file{(s.source.documents?.length ?? 0) === 1 ? "" : "s"}
                    {s.source.image_count ? ` and ${s.source.image_count} photos` : ""}
                  </div>
                ) : null}
              </button>
            ))}
            {scopes.length > 1 && (
              <button
                type="button"
                onClick={() => setComparing(comparing ? null : [scopes[1].id, scopes[0].id])}
                className="px-1 text-[13px] text-blue-700"
              >
                {comparing ? "Stop comparing" : "Compare two versions"}
              </button>
            )}
            <div className="border-t border-slate-200 pt-2">
              <div className="mb-1 px-1 text-[13px] font-semibold">What should change?</div>
              {latest && (
                <EditWithPrompt
                  key={latest.id}
                  projectId={projectId}
                  recordType="scope"
                  recordId={latest.id}
                  label={`v${latest.version}`}
                  asNewVersion
                  placeholder="e.g. Add the basement suite. Owner supplies appliances."
                  onApplied={(newId) => refresh(newId)}
                />
              )}
              <p className="m-0 mt-1 px-1 text-xs text-slate-500">
                Changes v{latest?.version}. You approve each change, and it is saved as v{(latest?.version ?? 0) + 1}; v
                {latest?.version} stays as it is.
              </p>
            </div>
          </Box>

          {/* ── the scope ─────────────────────────────────────────── */}
          <div className="min-w-0 space-y-3">
            {comparing ? (
              <CompareVersions scopes={scopes} pair={comparing} onChange={setComparing} />
            ) : (
              <>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-sm font-semibold uppercase tracking-wide text-slate-500">
                    Scope of work · v{current.version}
                    {current.id !== latest?.id && <span className="ml-2 normal-case text-orange-800">(an older version)</span>}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => setEditingId(current.id)}
                      className="rounded-md border border-slate-900 px-3 py-1.5 text-sm"
                    >
                      Edit
                    </button>
                    <details className="relative">
                      <summary className="cursor-pointer list-none rounded-md border border-slate-900 px-3 py-1.5 text-sm">
                        {pdfBusy ? "Building PDF…" : "PDF ▾"}
                      </summary>
                      <div className="absolute right-0 z-10 mt-1 w-56 rounded-md border border-slate-300 bg-white p-1 shadow">
                        <button
                          type="button"
                          disabled={pdfBusy !== null}
                          onClick={() => pdf("version")}
                          className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-slate-100"
                        >
                          This version (v{current.version})
                        </button>
                        <button
                          type="button"
                          disabled={pdfBusy !== null}
                          onClick={() => pdf("whole")}
                          className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-slate-100"
                        >
                          Whole-project Scope of Work
                        </button>
                      </div>
                    </details>
                  </div>
                </div>
                <ScopeDocumentPreview payload={normalizePayload(current.payload)} source={current.source} project={current.project} />
              </>
            )}

            {questions.length > 0 && (
              <Box className="border-orange-300 bg-orange-50 p-4">
                <div className="font-semibold text-orange-800">Still needed from the owner · {questions.length}</div>
                <ul className="m-0 mt-1 list-none space-y-1 p-0 text-[13px]">
                  {questions.map((q) => (
                    <li key={q.id}>
                      {q.question}{" "}
                      <Link
                        href={q.action ?? `/projects/${projectId}/overview`}
                        className="text-blue-700"
                      >
                        Answer
                      </Link>
                    </li>
                  ))}
                </ul>
              </Box>
            )}
          </div>

          {/* ── where it came from ────────────────────────────────── */}
          <Box className="h-fit space-y-2 p-3 text-[13px]">
            <Label>Where it came from</Label>
            {current.payload?._revision ? (
              <p className="m-0 text-slate-700">
                v{current.payload._revision.from_version} with a described change: &ldquo;
                {current.payload._revision.instruction}&rdquo;. Its files are those of v
                {current.payload._revision.from_version}.
              </p>
            ) : !current.source ? (
              <p className="m-0 text-slate-600">Written or imported by hand; no record of what it read.</p>
            ) : (
              <SourceList source={current.source} />
            )}
            <p className="m-0 border-t border-slate-200 pt-2 text-xs text-slate-500">
              Which file each line came from is not recorded yet.
            </p>
          </Box>
        </div>
      )}

      {showGenerate && (
        <GenerateModal
          projectId={projectId}
          selection={selection}
          onClose={() => setShowGenerate(false)}
          onChooseFiles={() => {
            setShowSelector(true);
            window.scrollTo({ top: 0, behavior: "smooth" });
          }}
          onDone={(scopeId) => {
            setShowGenerate(false);
            refresh(scopeId);
          }}
        />
      )}
    </div>
  );
}

function SourceList({ source }: { source: ScopeSource }) {
  const docs = source.documents ?? [];
  const read = docs.filter((d) => d.read);
  const unread = docs.filter((d) => !d.read);
  return (
    <>
      <div className="text-slate-700">
        {source.generated_at ? `Written ${new Date(source.generated_at).toLocaleDateString()}` : "Written"}
        {source.model ? ` by ${source.model}` : ""}
        {source.cost_usd ? ` · $${Number(source.cost_usd).toFixed(2)}` : ""}
      </div>
      {read.length > 0 && (
        <ul className="m-0 list-none space-y-0.5 p-0">
          {read.map((d) => (
            <li key={d.id} className="truncate" title={d.filename ?? ""}>
              {d.filename ?? "A file"}
              {d.reason ? <span className="text-slate-500"> · {d.reason}</span> : null}
            </li>
          ))}
        </ul>
      )}
      {source.image_count > 0 && <div className="text-slate-700">{source.image_count} photos uploaded with it</div>}
      {source.notes && (
        <details>
          <summary className="cursor-pointer text-slate-700">What it was told</summary>
          <p className="m-0 mt-1 whitespace-pre-wrap text-slate-600">{source.notes}</p>
        </details>
      )}
      {unread.length > 0 && (
        <details className="text-orange-800">
          <summary className="cursor-pointer">{unread.length} files it could not read</summary>
          <ul className="m-0 mt-1 list-none space-y-0.5 p-0 text-slate-700">
            {unread.map((d) => (
              <li key={d.id}>
                {d.filename ?? "A file"} · {d.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      {docs.length === 0 && !source.image_count && !source.notes && (
        <p className="m-0 text-slate-600">It was given no files, photos or notes.</p>
      )}
    </>
  );
}

/** Lines in one version and not the other, section by section. */
function CompareVersions({
  scopes,
  pair,
  onChange,
}: {
  scopes: ScopeRead[];
  pair: [string, string];
  onChange: (p: [string, string]) => void;
}) {
  const [a, b] = pair.map((id) => scopes.find((s) => s.id === id)) as [ScopeRead | undefined, ScopeRead | undefined];
  const lines = (s?: ScopeRead): Record<string, string[]> => {
    const p = normalizePayload(s?.payload);
    return {
      Overview: p.summary ? [p.summary] : [],
      "Work by trade": (p.categories ?? []).flatMap((c) => (c.items ?? []).map((i) => `${c.name}: ${i}`)),
      "Materials and specs": (p.materials ?? []).map((m) => [m.item, m.spec].filter(Boolean).join(" · ")),
      Risks: p.potentialIssues ?? [],
      "Site and access": p.accessConstraints ?? [],
      Permits: p.permits ?? [],
      Assumptions: p.assumptions ?? [],
      Exclusions: p.exclusions ?? [],
    };
  };
  const la = lines(a);
  const lb = lines(b);
  const picker = (value: string, set: (id: string) => void) => (
    <select value={value} onChange={(e) => set(e.target.value)} className="rounded border border-slate-400 px-1.5 py-1 text-sm">
      {scopes.map((s) => (
        <option key={s.id} value={s.id}>
          v{s.version}
        </option>
      ))}
    </select>
  );
  const sections = Object.keys(la);
  const changed = sections.filter((k) => {
    const A = new Set(la[k]);
    const B = new Set(lb[k]);
    return la[k].some((x) => !B.has(x)) || lb[k].some((x) => !A.has(x));
  });
  return (
    <Box className="space-y-3 p-4 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        Compare {picker(pair[0], (id) => onChange([id, pair[1]]))} with {picker(pair[1], (id) => onChange([pair[0], id]))}
      </div>
      {changed.length === 0 ? (
        <p className="m-0 text-slate-600">No differences.</p>
      ) : (
        changed.map((k) => {
          const A = new Set(la[k]);
          const B = new Set(lb[k]);
          return (
            <section key={k}>
              <div className="font-semibold">{k}</div>
              <ul className="m-0 list-none space-y-0.5 p-0">
                {la[k].filter((x) => !B.has(x)).map((x, i) => (
                  <li key={`r${i}`} className="text-red-800">
                    − {x}
                  </li>
                ))}
                {lb[k].filter((x) => !A.has(x)).map((x, i) => (
                  <li key={`a${i}`} className="text-emerald-800">
                    + {x}
                  </li>
                ))}
              </ul>
            </section>
          );
        })
      )}
      <p className="m-0 text-xs text-slate-500">− only in v{a?.version} · + only in v{b?.version}</p>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Generate modal
// ---------------------------------------------------------------------------

interface SelectedFile {
  file: File;
  caption: string;
}

function GenerateModal({
  projectId,
  selection,
  onClose,
  onDone,
  onChooseFiles,
}: {
  projectId: string;
  selection: SelectionSummary;
  onClose: () => void;
  onDone: (scopeId: string) => void;
  onChooseFiles: () => void;
}) {
  const [files, setFiles] = useState<SelectedFile[]>([]);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<AIJobStreamEvent | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [warnMsg, setWarnMsg] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const cost = useJobCost(projectId, "scope_analysis");

  useEffect(() => {
    return () => {
      sourceRef.current?.close();
    };
  }, []);

  const onPickFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []);
    setFiles((prev) => [...prev, ...picked.map((file) => ({ file, caption: "" }))]);
    // reset so picking the same file twice still fires the change handler
    e.target.value = "";
  };

  const submit = async () => {
    // Guard: require at least one usable input so the model isn't asked to
    // invent a scope from project metadata alone.
    if (files.length === 0 && selection.scopeIds.length === 0 && !notes.trim()) {
      setErrorMsg("Write the scope, or attach at least one photo or document, before generating.");
      return;
    }
    setBusy(true);
    setErrorMsg(null);
    setWarnMsg(null);
    setProgress(null);
    try {
      const images: AIJobImageInput[] = [];
      const documents: { media_type: string; data: string; caption?: string }[] = [];
      const skipped: string[] = [];
      const MAX_PDF_BYTES = 12 * 1024 * 1024;
      for (const sf of files) {
        if (sf.file.type.startsWith("image/")) {
          const { data, mediaType } = await resizeAndEncodeImage(sf.file);
          images.push({ media_type: mediaType, data, caption: sf.caption.trim() || undefined });
        } else if (sf.file.type === "application/pdf") {
          // Freshly-uploaded PDFs now feed the scope (were silently dropped before).
          if (sf.file.size > MAX_PDF_BYTES) {
            skipped.push(`${sf.file.name} — PDF over 12 MB; add it via the document library instead`);
            continue;
          }
          const data = await encodeFileBase64(sf.file);
          documents.push({ media_type: "application/pdf", data, caption: sf.caption.trim() || sf.file.name });
        } else {
          skipped.push(`${sf.file.name} — unsupported type (only images + PDFs can be read here)`);
        }
      }
      if (skipped.length) {
        setWarnMsg(`Not included: ${skipped.join("; ")}`);
      }

      const job = await createAIJob({
        project_id: projectId,
        kind: "scope_analysis",
        input: {
          images,
          documents,
          notes: notes.trim(),
          // Selected library documents (drawings/PDFs/photos). The worker fetches
          // each from R2 and skips any that are archived — so outdated drawings
          // left in the library never leak into the scope.
          document_ids: selection.scopeIds,
          // Pressing the button approved this amount; the server holds the job to it.
          approve_usd: cost ?? undefined,
        },
      });

      const es = streamAIJob(job.id);
      sourceRef.current = es;
      es.addEventListener("status", (ev) => {
        try {
          const payload = JSON.parse((ev as MessageEvent).data) as AIJobStreamEvent;
          setProgress(payload);
          if (payload.status === "done") {
            es.close();
            const scopeId = (payload.output as Record<string, unknown> | null)?.scope_id;
            if (typeof scopeId === "string") {
              onDone(scopeId);
            } else {
              setErrorMsg("Job finished but no scope was created.");
              setBusy(false);
            }
          } else if (payload.status === "error") {
            es.close();
            setErrorMsg(payload.error ?? "Unknown error");
            setBusy(false);
          }
        } catch (e) {
          // Swallow malformed events but surface in console for debugging.
          console.warn("scope SSE parse failed", e);
        }
      });
      es.addEventListener("error", () => {
        es.close();
        setErrorMsg("Lost connection to scope generation stream.");
        setBusy(false);
      });
    } catch (e) {
      setErrorMsg(String(e));
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      // A stray click outside closed the dialog and lost the typed scope
      // (rollout test 2026-10-14): only an empty dialog closes that way.
      onClick={busy || notes.trim() || files.length ? undefined : onClose}
    >
      <Card
        className="max-h-[88vh] w-full max-w-3xl overflow-y-auto p-6 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold" style={{ color: "var(--color-brio-accent)" }}>
            Generate Scope
          </h2>
          <button
            type="button"
            className="text-slate-400 hover:text-slate-700"
            disabled={busy}
            onClick={onClose}
          >
            ✕
          </button>
        </div>

        <div className="space-y-3">
          {/* Primary input: write the scope directly. Works on its own — ideal for
              change orders, extra work, and after-completion services. */}
          <label className="block text-sm font-medium text-slate-700">
            Scope of work — write it out here
            <span className="ml-1 text-xs font-normal text-slate-400">
              Type everything you know; Keystone turns it into a structured scope. Photos and drawings below are optional.
            </span>
            <textarea
              rows={7}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              disabled={busy}
              placeholder="e.g. Add a demising wall between suites 200 and 210; relocate two duplex receptacles; patch and paint affected drywall; supply and install one solid-core door with hardware…"
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-brio-primary)]"
            />
          </label>

          <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
            Optionally attach photos or drawings
          </div>

          {/* Library picker — choose which existing drawings/PDFs/photos feed the
              scope. Archived documents are hidden unless the toggle is on, so
              outdated drawings never leak into the scope of work. */}
          <div className="rounded-md border border-slate-200 p-3">
            {/* A summary, not the table. The table is a page section now: it
                is durable project state and does not belong inside a dialog
                you have to open a generation to reach. */}
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm font-medium text-slate-700">Documents this will read</span>
                <span className="tabular-nums text-sm font-semibold text-slate-800">
                  {selection.accuracyPct}% accuracy
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-600">
                {selection.scopeCount === 0 ? (
                  <>
                    <b>Nothing selected.</b> The scope will be written from your notes and any
                    files you add below. Close this and pick documents to do better.
                  </>
                ) : (
                  <>
                    <b>{selection.scopeCount}</b> of {selection.totalDocuments} documents, plus{" "}
                    <b>{selection.quantityCount}</b> set aside for the quantity takeoff.
                  </>
                )}
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  // Close and open the file chooser, rather than closing and
                  // leaving the user to find it.
                  onClose();
                  onChooseFiles();
                }}
                className="mt-1.5 text-xs font-medium text-blue-600 hover:underline"
              >
                Change the selection ↗
              </button>
            </div>
          </div>

          <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
            or upload new files
          </div>

          <label className="block text-sm font-medium text-slate-700">
            Photos, drawings, or PDFs
            <input
              type="file"
              multiple
              accept="image/*,application/pdf"
              onChange={onPickFiles}
              disabled={busy}
              className="mt-1 block w-full text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-slate-800 hover:file:bg-slate-200"
            />
          </label>

          {files.length > 0 && (
            <ul className="space-y-2">
              {files.map((sf, idx) => (
                <li
                  key={idx}
                  className="flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2"
                >
                  <span className="flex-1 truncate text-sm text-slate-700">
                    {sf.file.name}
                  </span>
                  <Input
                    placeholder="Caption (optional)"
                    value={sf.caption}
                    onChange={(e) => {
                      const v = e.target.value;
                      setFiles((prev) =>
                        prev.map((p, i) => (i === idx ? { ...p, caption: v } : p)),
                      );
                    }}
                    disabled={busy}
                    className="max-w-xs"
                  />
                  <button
                    type="button"
                    className="text-slate-400 hover:text-red-600 text-sm"
                    disabled={busy}
                    onClick={() =>
                      setFiles((prev) => prev.filter((_, i) => i !== idx))
                    }
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}

        </div>

        {progress && (
          <div className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">
            {progress.status === "running" || progress.status === "queued" ? (
              <span className="font-medium">Keystone is analyzing your documents…</span>
            ) : (
              <span className="font-medium">Keystone · {progress.status}</span>
            )}{" "}
            · tokens out {progress.output_tokens} · cost ${progress.cost_usd}
            {progress.model ? ` · ${progress.model}` : ""}
          </div>
        )}

        {warnMsg && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            {warnMsg}
          </div>
        )}

        {errorMsg && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {errorMsg}
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || cost === null}>
            {busy
              ? "Keystone is working…"
              : cost === null
                ? "Working out the cost…"
                : `Generate with Keystone · ${upTo(cost)}`}
          </Button>
        </div>
      </Card>
    </div>
  );
}

// Cap the longest edge at 1600px and re-encode as JPEG q=0.75. Phone photos
// (4–8 MB) compress to ~150–300 KB without losing the detail Opus needs to
// read tile/fixture/condition cues. Without this, a multi-image upload
// produces a multi-MB JSON body that Cloudflare 502s before reaching
// Anthropic's image-size limit.
const MAX_EDGE_PX = 1600;
const JPEG_QUALITY = 0.75;

async function resizeAndEncodeImage(
  file: File,
): Promise<{ data: string; mediaType: string }> {
  const bitmap = await loadImageBitmap(file);
  const scale = Math.min(1, MAX_EDGE_PX / Math.max(bitmap.width, bitmap.height));
  const w = Math.max(1, Math.round(bitmap.width * scale));
  const h = Math.max(1, Math.round(bitmap.height * scale));

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas 2d context unavailable");
  ctx.drawImage(bitmap, 0, 0, w, h);
  if ("close" in bitmap && typeof bitmap.close === "function") bitmap.close();

  const blob: Blob = await new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("toBlob returned null"))),
      "image/jpeg",
      JPEG_QUALITY,
    );
  });
  const data = await blobToBase64(blob);
  return { data, mediaType: "image/jpeg" };
}

/** Base64-encode a file as-is (no re-encode) — used for PDF uploads that feed
 *  the scope. A `File` is a `Blob`, so `blobToBase64` handles it directly. */
async function encodeFileBase64(file: File): Promise<string> {
  return blobToBase64(file);
}

async function loadImageBitmap(file: File): Promise<ImageBitmap | HTMLImageElement> {
  if (typeof createImageBitmap === "function") {
    try {
      return await createImageBitmap(file);
    } catch {
      // fall through to <img> path (Safari quirks on HEIC, etc.)
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("image load failed"));
      el.src = url;
    });
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(blob);
  });
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

function ScopeEditor({
  projectId,
  scopeId,
  onBack,
}: {
  projectId: string;
  scopeId: string;
  onBack: () => void;
}) {
  const [scope, setScope] = useState<ScopeRead | null>(null);
  const [payload, setPayload] = useState<ScopePayload | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  // S1.5 — post-save feedback modal trigger. `null` = modal closed.
  const [feedback, setFeedback] = useState<{
    marker: string;
    value: string;
    output: Record<string, unknown>;
  } | null>(null);

  useEffect(() => {
    getScope(scopeId)
      .then((s) => {
        setScope(s);
        setPayload(normalizePayload(s.payload));
      })
      .catch((e) => setError(errorMessage(e)));
  }, [scopeId]);

  const update = (patch: Partial<ScopePayload>) => {
    setPayload((prev) => (prev ? { ...prev, ...patch } : prev));
  };

  const save = async () => {
    if (!payload || !scope) return;
    setSaving(true);
    setError(null);
    try {
      await updateScope(scope.id, payload);
      setSavedAt(Date.now());
      // S1.5 — open the post-save feedback modal so the user can tag
      // why they edited this scope. The modal is silent if there's no
      // originating AI run (manually-created scope).
      setFeedback({
        marker: "scope_id",
        value: scope.id,
        output: payload as unknown as Record<string, unknown>,
      });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  // Save current edits, then render + store the PDF and open it. Saving first
  // guarantees the PDF matches what is on screen (the backend renders from the
  // persisted payload).
  const generatePdf = async () => {
    if (!payload || !scope) return;
    setPdfBusy(true);
    setError(null);
    try {
      await updateScope(scope.id, payload);
      setSavedAt(Date.now());
      const result = await generateScopePdf(scope.id);
      window.open(result.download_url, "_blank", "noopener");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPdfBusy(false);
    }
  };

  if (error && !scope) {
    return (
      <div className="space-y-4">
        <button onClick={onBack} className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to scopes
        </button>
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      </div>
    );
  }
  if (!scope || !payload) {
    return <div className="text-slate-400">Loading scope…</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="text-sm text-slate-500 hover:text-slate-700">
          ← Back to scopes
        </button>
        <div className="flex items-center gap-2">
          {savedAt && (
            <span className="text-xs text-emerald-600">
              Saved {new Date(savedAt).toLocaleTimeString()}
            </span>
          )}
          <Button onClick={save} disabled={saving || pdfBusy}>
            {saving ? "Saving…" : "Save"}
          </Button>
          <Button
            variant="secondary"
            onClick={generatePdf}
            disabled={pdfBusy || saving}
          >
            {pdfBusy ? "Generating PDF…" : "Generate PDF"}
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <SourcePanel source={scope.source} />

      {/* Change the scope by describing the change. Proposes only; every
          change is reviewed and accepted one at a time, and the edit is
          snapshotted so it can be walked back. */}
      <EditWithPrompt
        projectId={projectId}
        recordType="scope"
        recordId={scopeId}
        label={`v${scope.version}`}
        onApplied={() => {
          getScope(scopeId)
            .then((s2) => {
              setScope(s2);
              setPayload(normalizePayload(s2.payload));
            })
            .catch((e) => setError(errorMessage(e)));
        }}
      />

      <Card className="p-4 space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Scope v{scope.version}
          {scope.source && (
            <span className="ml-2 font-normal normal-case tracking-normal text-slate-400">
              · generated by Keystone
            </span>
          )}
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="text-slate-600">Reference</span>
            <Input
              value={payload.reference ?? ""}
              onChange={(e) => update({ reference: e.target.value })}
            />
          </label>
          <label className="block text-sm">
            <span className="text-slate-600">Date</span>
            <Input
              value={payload.date ?? ""}
              onChange={(e) => update({ date: e.target.value })}
            />
          </label>
        </div>
        <label className="block text-sm">
          <span className="text-slate-600">
            Summary{" "}
            <span className="text-xs text-slate-400">
              — plain-language overview shown at the top of the document
            </span>
          </span>
          <textarea
            rows={4}
            value={payload.summary ?? ""}
            onChange={(e) => update({ summary: e.target.value })}
            placeholder="3–5 sentences describing the job, the headline transformation, and the key risks…"
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-brio-primary)]"
          />
        </label>
      </Card>

      <CategoriesEditor
        categories={payload.categories ?? []}
        onChange={(categories) => update({ categories })}
      />

      <MaterialsEditor
        materials={payload.materials ?? []}
        onChange={(materials) => update({ materials })}
      />

      <StringListEditor
        title="Potential Issues"
        items={payload.potentialIssues ?? []}
        onChange={(potentialIssues) => update({ potentialIssues })}
      />

      <StringListEditor
        title="Access & Site Constraints"
        items={payload.accessConstraints ?? []}
        onChange={(accessConstraints) => update({ accessConstraints })}
      />

      <StringListEditor
        title="Permits Required"
        items={payload.permits ?? []}
        onChange={(permits) => update({ permits })}
      />

      <StringListEditor
        title="Assumptions"
        items={payload.assumptions ?? []}
        onChange={(assumptions) => update({ assumptions })}
      />

      <StringListEditor
        title="Exclusions"
        items={payload.exclusions ?? []}
        onChange={(exclusions) => update({ exclusions })}
      />

      <ScopeDocumentPreview payload={payload} source={scope.source} project={scope.project} />

      {/* TODO Track D: PDF — a working "Generate PDF" button lives here once
          we pick between Puppeteer-sidecar and WeasyPrint. The PDF renders
          the same document the ScopeDocumentPreview shows. */}

      {feedback && (
        <SkillFeedbackModal
          skillId="scope_analyzer"
          artifactMarker={feedback.marker}
          artifactValue={feedback.value}
          acceptedOutput={feedback.output}
          onClose={() => setFeedback(null)}
        />
      )}
    </div>
  );
}

function normalizePayload(p: ScopePayload | undefined | null): ScopePayload {
  return {
    reference: p?.reference ?? "",
    date: p?.date ?? new Date().toISOString().slice(0, 10),
    summary: typeof p?.summary === "string" ? p.summary : "",
    categories: Array.isArray(p?.categories) ? p!.categories : [],
    materials: Array.isArray(p?.materials) ? p!.materials : [],
    potentialIssues: Array.isArray(p?.potentialIssues) ? p!.potentialIssues : [],
    accessConstraints: Array.isArray(p?.accessConstraints) ? p!.accessConstraints : [],
    permits: Array.isArray(p?.permits) ? p!.permits : [],
    assumptions: Array.isArray(p?.assumptions) ? p!.assumptions : [],
    exclusions: Array.isArray(p?.exclusions) ? p!.exclusions : [],
  };
}

// ---------------------------------------------------------------------------
// Reusable section editors
// ---------------------------------------------------------------------------

function StringListEditor({
  title,
  items,
  onChange,
}: {
  title: string;
  items: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <Card className="p-4 space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          {title}
        </h3>
        <Button variant="ghost" onClick={() => onChange([...items, ""])}>
          + Add row
        </Button>
      </div>
      {items.length === 0 ? (
        <div className="text-xs text-slate-400">No entries.</div>
      ) : (
        <ul className="space-y-2">
          {items.map((row, idx) => (
            <li key={idx} className="flex items-center gap-2">
              <Input
                value={row}
                onChange={(e) => {
                  const next = items.slice();
                  next[idx] = e.target.value;
                  onChange(next);
                }}
              />
              <button
                type="button"
                className="text-slate-400 hover:text-red-600 text-sm"
                onClick={() => onChange(items.filter((_, i) => i !== idx))}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function CategoriesEditor({
  categories,
  onChange,
}: {
  categories: ScopeCategory[];
  onChange: (next: ScopeCategory[]) => void;
}) {
  const safe = useMemo(
    () =>
      categories.map((c) => ({
        name: c?.name ?? "",
        items: Array.isArray(c?.items) ? c.items : [],
      })),
    [categories],
  );

  const setCat = (idx: number, patch: Partial<ScopeCategory>) => {
    onChange(safe.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  };

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Work Categories
        </h3>
        <Button
          variant="ghost"
          onClick={() => onChange([...safe, { name: "", items: [] }])}
        >
          + Add category
        </Button>
      </div>
      {safe.length === 0 ? (
        <div className="text-xs text-slate-400">No categories.</div>
      ) : (
        <ul className="space-y-3">
          {safe.map((cat, idx) => (
            <li key={idx} className="rounded-md border border-slate-200 p-3 space-y-2">
              <div className="flex items-center gap-2">
                <Input
                  value={cat.name}
                  placeholder="Category name (e.g. Plumbing)"
                  onChange={(e) => setCat(idx, { name: e.target.value })}
                />
                <button
                  type="button"
                  className="text-slate-400 hover:text-red-600 text-sm"
                  onClick={() => onChange(safe.filter((_, i) => i !== idx))}
                >
                  Remove category
                </button>
              </div>
              <div className="pl-2 border-l-2 border-slate-100 space-y-1">
                {cat.items.length === 0 ? (
                  <div className="text-xs text-slate-400">No items.</div>
                ) : (
                  cat.items.map((row, j) => (
                    <div key={j} className="flex items-center gap-2">
                      <Input
                        value={row}
                        onChange={(e) => {
                          const items = cat.items.slice();
                          items[j] = e.target.value;
                          setCat(idx, { items });
                        }}
                      />
                      <button
                        type="button"
                        className="text-slate-400 hover:text-red-600 text-xs"
                        onClick={() =>
                          setCat(idx, {
                            items: cat.items.filter((_, k) => k !== j),
                          })
                        }
                      >
                        Remove
                      </button>
                    </div>
                  ))
                )}
                <Button
                  variant="ghost"
                  onClick={() => setCat(idx, { items: [...cat.items, ""] })}
                >
                  + Add item
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function MaterialsEditor({
  materials,
  onChange,
}: {
  materials: ScopeMaterial[];
  onChange: (next: ScopeMaterial[]) => void;
}) {
  const safe = useMemo(
    () =>
      materials.map((m) => ({
        item: m?.item ?? "",
        spec: m?.spec ?? "",
        notes: m?.notes ?? "",
      })),
    [materials],
  );

  const setMat = (idx: number, patch: Partial<ScopeMaterial>) => {
    onChange(safe.map((m, i) => (i === idx ? { ...m, ...patch } : m)));
  };

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Materials & Specifications
        </h3>
        <Button
          variant="ghost"
          onClick={() => onChange([...safe, { item: "", spec: "", notes: "" }])}
        >
          + Add material
        </Button>
      </div>
      {safe.length === 0 ? (
        <div className="text-xs text-slate-400">No materials.</div>
      ) : (
        <ul className="space-y-2">
          {safe.map((mat, idx) => (
            <li key={idx} className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center">
              <div className="md:col-span-3">
                <Input
                  value={mat.item}
                  placeholder="Item"
                  onChange={(e) => setMat(idx, { item: e.target.value })}
                />
              </div>
              <div className="md:col-span-4">
                <Input
                  value={mat.spec}
                  placeholder="Spec (or TBD)"
                  onChange={(e) => setMat(idx, { spec: e.target.value })}
                />
              </div>
              <div className="md:col-span-4">
                <Input
                  value={mat.notes}
                  placeholder="Notes (allowance, lead time, alternatives)"
                  onChange={(e) => setMat(idx, { notes: e.target.value })}
                />
              </div>
              <div className="md:col-span-1 text-right">
                <button
                  type="button"
                  className="text-slate-400 hover:text-red-600 text-xs"
                  onClick={() => onChange(safe.filter((_, i) => i !== idx))}
                >
                  Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Source panel — what Keystone analyzed to produce this scope
// ---------------------------------------------------------------------------

function SourcePanel({ source }: { source?: ScopeSource | null }) {
  if (!source) {
    return (
      <Card className="p-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Source
        </h2>
        <p className="mt-2 text-sm text-slate-400">
          This scope was created manually or imported — there is no Keystone
          analysis on record for it.
        </p>
      </Card>
    );
  }

  const meta = source.project_metadata ?? {};
  const docCount = source.image_count;
  const noteWords = source.notes ? source.notes.trim().split(/\s+/).length : 0;
  const lightInput = docCount <= 2 && noteWords < 80;

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          What Keystone analyzed
        </h2>
        {source.generated_at && (
          <span className="text-xs text-slate-400">
            {new Date(source.generated_at).toLocaleString()}
          </span>
        )}
      </div>

      {/* Coverage banner — makes the depth of input obvious at a glance. */}
      <div
        className={`rounded-md px-3 py-2 text-sm ${
          lightInput
            ? "bg-amber-50 text-amber-800"
            : "bg-slate-50 text-slate-700"
        }`}
      >
        Keystone worked from{" "}
        <strong>
          {docCount} image{docCount === 1 ? "" : "s"}
        </strong>{" "}
        and{" "}
        <strong>
          {noteWords} word{noteWords === 1 ? "" : "s"} of notes
        </strong>
        .
        {lightInput && (
          <>
            {" "}
            This is a light input set — much of the scope below is inferred
            from typical renovation practice, not from documented site
            conditions. Verify against real project photos and drawings before
            relying on it.
          </>
        )}
      </div>

      <div>
        <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
          Images ({docCount})
        </div>
        {docCount === 0 ? (
          <div className="text-sm text-slate-400">None provided.</div>
        ) : (
          <ul className="mt-1 space-y-0.5">
            {source.images.map((img, i) => (
              <li key={i} className="text-sm text-slate-700">
                {i + 1}.{" "}
                {img.caption || (
                  <span className="text-slate-400">(no caption)</span>
                )}
                {img.media_type && (
                  <span className="ml-1 text-xs text-slate-400">
                    · {img.media_type}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
          Notes &amp; instructions given to Keystone
        </div>
        {source.notes ? (
          <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700">
            {source.notes}
          </p>
        ) : (
          <div className="text-sm text-slate-400">None provided.</div>
        )}
      </div>

      {Object.keys(meta).length > 0 && (
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
            Project context
          </div>
          <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 text-sm md:grid-cols-3">
            {Object.entries(meta).map(([k, v]) => (
              <div key={k}>
                <dt className="inline text-slate-400">
                  {k.replace(/_/g, " ")}:{" "}
                </dt>
                <dd className="inline text-slate-700">{String(v)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      <div className="border-t border-slate-100 pt-2 text-xs text-slate-400">
        {source.model && <>Model {source.model} · </>}
        {source.input_tokens != null && (
          <>{source.input_tokens.toLocaleString()} in</>
        )}
        {source.output_tokens != null && (
          <> / {source.output_tokens.toLocaleString()} out tokens</>
        )}
        {source.cost_usd != null && <> · ${source.cost_usd}</>}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Document preview — the scope as it reads when it goes out
// ---------------------------------------------------------------------------

function DocHeading({ children }: { children: React.ReactNode }) {
  return (
    <h4
      className="mb-1 text-xs font-semibold uppercase tracking-wide"
      style={{ color: "var(--color-brio-primary)" }}
    >
      {children}
    </h4>
  );
}

function DocList({ title, items }: { title: string; items: string[] }) {
  return (
    <section>
      <DocHeading>{title}</DocHeading>
      <ul className="ml-5 list-disc space-y-0.5 text-slate-700">
        {items.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>
    </section>
  );
}

function ScopeDocumentPreview({
  payload,
  source,
  project,
}: {
  payload: ScopePayload;
  source?: ScopeSource | null;
  project?: Record<string, unknown>;
}) {
  const [mode, setMode] = useState<"internal" | "client">("internal");
  const isClient = mode === "client";

  // The project record first: an edited version has no source (rollout test 2026-10-14).
  const meta = { ...(source?.project_metadata ?? {}), ...(project ?? {}) };
  const projectName = (meta.name as string) || "—";
  const address = (meta.address as string) || "";

  const categories = (payload.categories ?? []).filter(
    (c) => (c.name ?? "").trim() || (c.items ?? []).some((i) => i.trim()),
  );
  const materials = (payload.materials ?? []).filter((m) => (m.item ?? "").trim());
  const clean = (xs?: string[]) => (xs ?? []).filter((s) => s.trim());
  const issues = clean(payload.potentialIssues);
  const permits = clean(payload.permits);
  const assumptions = clean(payload.assumptions);
  const exclusions = clean(payload.exclusions);
  const access = clean(payload.accessConstraints);

  return (
    <Card className="overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-4 py-3">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Document preview — how this scope reads when it goes out
        </h3>
        <div className="flex overflow-hidden rounded-md border border-slate-300 text-xs">
          <button
            type="button"
            onClick={() => setMode("internal")}
            className={`px-3 py-1 ${
              !isClient
                ? "bg-[var(--color-brio-primary)] text-white"
                : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            Internal
          </button>
          <button
            type="button"
            onClick={() => setMode("client")}
            className={`px-3 py-1 ${
              isClient
                ? "bg-[var(--color-brio-primary)] text-white"
                : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            Client
          </button>
        </div>
      </div>

      <div className="space-y-5 px-6 py-6 text-sm text-slate-800">
        <div className="space-y-0.5">
          <div
            className="text-lg font-semibold"
            style={{ color: "var(--color-brio-accent)" }}
          >
            Built By Brio — Scope of Work
          </div>
          <div className="text-slate-600">{projectName}</div>
          {address && <div className="text-slate-500">{address}</div>}
          <div className="text-xs text-slate-400">
            {payload.reference || "—"} · {payload.date || "—"}
          </div>
        </div>

        {payload.summary && (
          <section>
            <DocHeading>Overview</DocHeading>
            <p className="whitespace-pre-wrap leading-relaxed">
              {payload.summary}
            </p>
          </section>
        )}

        {source?.notes && (
          <section>
            <DocHeading>
              {isClient
                ? "Your Requests & Project Notes"
                : "Client Requests / Instructions Given to Keystone"}
            </DocHeading>
            <p className="whitespace-pre-wrap leading-relaxed text-slate-600">
              {source.notes}
            </p>
          </section>
        )}

        {categories.length > 0 && (
          <section>
            <DocHeading>Scope of Work</DocHeading>
            <div className="space-y-3">
              {categories.map((c, i) => (
                <div key={i}>
                  <div className="font-medium text-slate-800">
                    {c.name || "—"}
                  </div>
                  <ul className="ml-5 list-disc text-slate-700">
                    {(c.items ?? [])
                      .filter((it) => it.trim())
                      .map((it, j) => (
                        <li key={j}>{it}</li>
                      ))}
                  </ul>
                </div>
              ))}
            </div>
          </section>
        )}

        {materials.length > 0 && (
          <section>
            <DocHeading>Materials &amp; Specifications</DocHeading>
            <table className="w-full text-left">
              <thead className="text-xs uppercase text-slate-400">
                <tr>
                  <th className="py-1 pr-3">Item</th>
                  <th className="py-1 pr-3">Spec</th>
                  <th className="py-1">Notes</th>
                </tr>
              </thead>
              <tbody>
                {materials.map((m, i) => (
                  <tr key={i} className="border-t border-slate-100 align-top">
                    <td className="py-1 pr-3">{m.item}</td>
                    <td className="py-1 pr-3 text-slate-600">
                      {m.spec || "—"}
                    </td>
                    <td className="py-1 text-slate-500">{m.notes || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {permits.length > 0 && (
          <DocList title="Permits Required" items={permits} />
        )}
        {access.length > 0 && (
          <DocList title="Access & Site Constraints" items={access} />
        )}
        {issues.length > 0 && (
          <DocList
            title={
              isClient
                ? "Site Conditions to Verify Before Finalizing Pricing"
                : "Potential Issues & Risks"
            }
            items={issues}
          />
        )}
        {assumptions.length > 0 && (
          <DocList title="Assumptions" items={assumptions} />
        )}
        {exclusions.length > 0 && (
          <DocList title="Exclusions — Not Included" items={exclusions} />
        )}

        <div className="border-t border-slate-200 pt-3 text-xs text-slate-400">
          {isClient
            ? "Scope of work provided for your review. Pricing follows in a separate estimate. Built By Brio · builtbybrio.com"
            : "Drafted by Keystone · internal review copy — confirm site conditions and edit above before sharing with the client."}
        </div>
      </div>
    </Card>
  );
}
