"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getDocumentSelection,
  putDocumentSelection,
  type SelectableDocument,
  type SelectionResponse,
} from "@/lib/documentSelection";
import {
  DOCUMENT_CATEGORIES,
  getPreviewUrl,
  updateDocumentCategory,
  type DocumentCategory,
} from "@/lib/documents";

/**
 * Every document in the project, and two decisions about each one.
 *
 * What this replaces: a truncated list of filenames with one checkbox, sitting
 * on top of a backend that then applied `category === "drawing"` before the CAD
 * extractor ever saw a file. On 105-107 Maple that combination meant 4 of
 * the 25 issuer-stamped drawing sheets were considered, and nothing on screen
 * said so.
 *
 * Two columns, because the two consumers want different files:
 *   SCOPE       what the model reads to write the narrative (PDFs, images)
 *   QUANTITIES  what the CAD extractor measures (DWG, DXF)
 * A permit-set PDF is usefully both. Most documents are neither.
 *
 * Nothing is ticked on load. "Select recommended" is a starting point for
 * projects with hundreds of files, and every tick carries the reason it was
 * made, so a recommendation can be argued with rather than trusted.
 */

const FORMAT_STYLE: Record<string, string> = {
  cad: "bg-violet-100 text-violet-700",
  pdf: "bg-sky-100 text-sky-700",
  image: "bg-emerald-100 text-emerald-700",
  text: "bg-amber-100 text-amber-800",
  other: "bg-slate-100 text-slate-500",
};

type Filter = "all" | "recommended" | "selected" | "drawings" | "photos" | "unusable";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "recommended", label: "Recommended" },
  { id: "selected", label: "Selected" },
  { id: "drawings", label: "Drawings & CAD" },
  { id: "photos", label: "Photos" },
  { id: "unusable", label: "Can't be read" },
];

function bytes(n: number | null): string {
  if (!n) return "";
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export interface SelectionSummary {
  /** Ids ticked for SCOPE. The generate call sends exactly these. */
  scopeIds: string[];
  scopeCount: number;
  quantityCount: number;
  accuracyPct: number;
  totalDocuments: number;
}

export default function DocumentSelector({
  projectId,
  disabled,
  onChange,
}: {
  projectId: string;
  disabled?: boolean;
  onChange: (summary: SelectionSummary) => void;
}) {
  const [data, setData] = useState<SelectionResponse | null>(null);
  const [scope, setScope] = useState<Set<string>>(new Set());
  const [qty, setQty] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [err, setErr] = useState<string | null>(null);
  const dirty = useRef(false);

  useEffect(() => {
    getDocumentSelection(projectId)
      .then((res) => {
        setData(res);
        setScope(new Set(res.documents.filter((d) => d.use_for_scope).map((d) => d.document_id)));
        setQty(new Set(res.documents.filter((d) => d.use_for_quantities).map((d) => d.document_id)));
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [projectId]);

  // The page above needs the ticked scope ids to generate with, and the counts
  // to show in the Generate dialog without duplicating this whole table there.
  useEffect(() => {
    onChange({
      scopeIds: Array.from(scope),
      scopeCount: scope.size,
      quantityCount: qty.size,
      accuracyPct: data?.accuracy?.coverage_pct ?? 0,
      totalDocuments: data?.total_documents ?? 0,
    });
  }, [scope, qty, data, onChange]);

  // Debounced persistence. The selection outlives this modal — the CAD takeoff
  // and the readiness grade both read it later — so it is saved as you tick
  // rather than only when a scope is generated.
  const save = useCallback(
    (nextScope: Set<string>, nextQty: Set<string>) => {
      if (!data) return;
      const ids = new Set([...nextScope, ...nextQty]);
      const entries = Array.from(ids).map((id) => ({
        document_id: id,
        use_for_scope: nextScope.has(id),
        use_for_quantities: nextQty.has(id),
      }));
      setSaveState("saving");
      putDocumentSelection(projectId, entries)
        .then((res) => {
          setData(res);
          setSaveState("saved");
        })
        .catch((e) => {
          setErr(e instanceof Error ? e.message : String(e));
          setSaveState("error");
        });
    },
    [data, projectId],
  );

  useEffect(() => {
    if (!dirty.current) return;
    const t = setTimeout(() => {
      // Cleared before saving: the save stores the server's reply, which makes
      // a new `save` and re-runs this effect. With the flag left set, it saved
      // again every ~0.7 s for as long as the page was open (42 PUTs in a few
      // minutes, rollout test 2026-10-14).
      dirty.current = false;
      save(scope, qty);
    }, 700);
    return () => clearTimeout(t);
  }, [scope, qty, save]);

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, id: string) => {
    dirty.current = true;
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setter(next);
  };

  const selectRecommended = () => {
    if (!data) return;
    dirty.current = true;
    setScope(new Set(data.documents.filter((d) => d.recommend_scope).map((d) => d.document_id)));
    setQty(new Set(data.documents.filter((d) => d.recommend_quantities).map((d) => d.document_id)));
  };

  const clearAll = () => {
    dirty.current = true;
    setScope(new Set());
    setQty(new Set());
  };

  const visible = useMemo(() => {
    const docs = data?.documents ?? [];
    const needle = q.trim().toLowerCase();
    return docs.filter((d) => {
      if (needle && !d.filename.toLowerCase().includes(needle)) return false;
      switch (filter) {
        case "recommended":
          return d.recommend_scope || d.recommend_quantities;
        case "selected":
          return scope.has(d.document_id) || qty.has(d.document_id);
        case "drawings":
          return d.format === "cad" || d.format === "pdf";
        case "photos":
          return d.format === "image";
        case "unusable":
          return !d.can_scope && !d.can_quantities;
        default:
          return true;
      }
    });
  }, [data, q, filter, scope, qty]);

  const changeCategory = async (d: SelectableDocument, category: DocumentCategory) => {
    setErr(null);
    try {
      await updateDocumentCategory(d.document_id, category);
      // The category drives the recommendation, so re-read rather than patch
      // locally — otherwise the reason shown next to the row goes stale.
      const res = await getDocumentSelection(projectId);
      setData(res);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const openPreview = async (id: string) => {
    setErr(null);
    try {
      const p = await getPreviewUrl(id);
      if (p.previewable && p.preview_url) window.open(p.preview_url, "_blank", "noopener");
      else setErr(p.reason ?? "This file cannot be previewed.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  if (err && !data) {
    return <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>;
  }
  if (!data) return <div className="text-sm text-slate-400">Loading documents…</div>;

  const acc = data.accuracy;
  const pct = acc?.coverage_pct ?? 0;
  const dupes = data.documents.filter((d) => d.duplicate_of).length;

  return (
    <div className="space-y-3">
      {/* Accuracy. The number moves as you tick, because the grade is computed
          from the selection rather than from what happens to be uploaded. */}
      <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm font-medium text-slate-700">
            Estimate accuracy from this selection
          </span>
          <span className="tabular-nums text-lg font-semibold text-slate-800">{pct}%</span>
        </div>
        <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-slate-200">
          <div
            className="h-full rounded-full bg-blue-600 transition-all duration-300"
            style={{ width: `${Math.max(2, Math.min(100, pct))}%` }}
          />
        </div>
        {acc?.headline && <p className="mt-2 text-xs text-slate-600">{acc.headline}</p>}
        {acc?.allowance_divisions?.length > 0 && (
          <p className="mt-1 text-xs text-slate-500">
            <b>Priced as an allowance:</b> {acc.allowance_divisions.join(", ")}
          </p>
        )}
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
          <span>{data.selected_scope} for scope</span>
          <span>{data.selected_quantities} for quantities</span>
          <span>{data.total_documents} in the project</span>
          {dupes > 0 && <span className="text-amber-700">{dupes} duplicates found</span>}
          <span className="ml-auto">
            {saveState === "saving" && "Saving…"}
            {saveState === "saved" && "Saved"}
            {saveState === "error" && <span className="text-red-600">Not saved</span>}
          </span>
        </div>
      </div>

      {err && (
        <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{err}</div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search filenames"
          className="min-w-[12rem] min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
        />
        <button
          type="button"
          onClick={selectRecommended}
          disabled={disabled}
          className="rounded bg-blue-600 px-2.5 py-1 text-xs font-medium text-white disabled:opacity-60"
        >
          Select recommended
        </button>
        <button
          type="button"
          onClick={clearAll}
          disabled={disabled}
          className="rounded border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-600"
        >
          Clear
        </button>
      </div>

      <div className="flex flex-wrap gap-1">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setFilter(f.id)}
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
              filter === f.id
                ? "bg-slate-800 text-white"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="max-h-[32rem] overflow-y-auto rounded border border-slate-200">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10 bg-slate-100 text-[10px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="w-14 px-2 py-1.5 font-medium">Scope</th>
              <th className="w-14 px-2 py-1.5 font-medium">Qty</th>
              <th className="px-2 py-1.5 text-left font-medium">Document</th>
              <th className="w-40 px-2 py-1.5 text-left font-medium">Category</th>
              <th className="w-16 px-2 py-1.5 font-medium" />
            </tr>
          </thead>
          <tbody>
            {visible.map((d) => {
              const isDupe = !!d.duplicate_of;
              return (
                <tr
                  key={d.document_id}
                  className={`border-t border-slate-100 align-top ${isDupe ? "bg-amber-50/40" : ""}`}
                >
                  <td className="px-2 py-1.5 text-center">
                    <input
                      type="checkbox"
                      checked={scope.has(d.document_id)}
                      disabled={disabled || !d.can_scope}
                      onChange={() => toggle(scope, setScope, d.document_id)}
                      title={d.can_scope ? "Read this for the scope" : d.scope_blocked_reason ?? ""}
                    />
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <input
                      type="checkbox"
                      checked={qty.has(d.document_id)}
                      disabled={disabled || !d.can_quantities}
                      onChange={() => toggle(qty, setQty, d.document_id)}
                      title={
                        d.can_quantities
                          ? "Measure this for quantities"
                          : "Only DWG and DXF files can be measured"
                      }
                    />
                  </td>
                  <td className="px-2 py-1.5">
                    {/* Full filename, wrapped rather than truncated: the sheet
                        code and revision date live at the end of the name, and
                        an ellipsis hides exactly the part that identifies it. */}
                    <span className="break-words text-slate-800">{d.filename}</span>
                    <div className="mt-0.5 flex flex-wrap items-center gap-1">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase ${
                          FORMAT_STYLE[d.format] ?? FORMAT_STYLE.other
                        }`}
                      >
                        {d.format}
                      </span>
                      {d.discipline && d.discipline !== "general" && (
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
                          {d.discipline}?
                        </span>
                      )}
                      {isDupe && (
                        <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
                          duplicate
                        </span>
                      )}
                      {d.size_bytes ? (
                        <span className="text-[10px] text-slate-400">{bytes(d.size_bytes)}</span>
                      ) : null}
                      <span className="text-[11px] text-slate-500">
                        {d.scope_reason ?? d.scope_blocked_reason ?? ""}
                      </span>
                    </div>
                  </td>
                  <td className="px-2 py-1.5">
                    {/* Editable in place. The category no longer gates what the
                        AI reads, but it still drives the recommendation and the
                        filing, and a wrong one is easiest to fix where it is
                        noticed. */}
                    <select
                      value={d.category}
                      disabled={disabled}
                      onChange={(e) => changeCategory(d, e.target.value as DocumentCategory)}
                      className="w-full rounded border border-slate-200 bg-white px-1 py-0.5 text-xs"
                    >
                      {DOCUMENT_CATEGORIES.some((c) => c.value === d.category) ? null : (
                        <option value={d.category}>{d.category}</option>
                      )}
                      {DOCUMENT_CATEGORIES.map((c) => (
                        <option key={c.value} value={c.value}>
                          {c.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <button
                      type="button"
                      onClick={() => openPreview(d.document_id)}
                      className="text-xs font-medium text-blue-600 hover:underline"
                    >
                      View
                    </button>
                  </td>
                </tr>
              );
            })}
            {visible.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-sm text-slate-400">
                  {data.documents.length === 0
                    ? "No documents in this project yet — upload below."
                    : "Nothing matches that filter."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
