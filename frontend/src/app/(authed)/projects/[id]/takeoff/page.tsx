"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";

import { Box, Label } from "@/components/kit";
import { DrawingPane } from "@/components/takeoff/DrawingPane";
import { PdfPagePane } from "@/components/takeoff/PdfPagePane";
import { WorkspaceError } from "@/components/WorkspaceError";
import type { AnalysisResponse } from "@/lib/drawingAnalysis";
import { listDocuments, type DocumentRead } from "@/lib/documents";
import {
  checkMeasurement,
  checkMeasurements,
  getTakeoff,
  listMeasurements,
  measurementLabel,
  readDrawings,
  signOff,
  STATUS_LABEL,
  type Measurement,
} from "@/lib/takeoff";

/**
 * Takeoff — one page for what used to be Drawing analysis, the six Quantities
 * pages and the workspace Takeoff tool. Three steps, in order:
 *
 *   1. Read the drawings   — every CAD file, into measurements (a new draft).
 *   2. Check each one      — Correct / Fix it / Don't count, beside the drawing.
 *   3. Sign off the set    — catches a missing floor or a failed sheet, and is
 *                            what lets the estimate use the numbers.
 *
 * Measuring on a PDF still happens on the per-discipline page ("Measure on a
 * PDF" in the tab's second row); the rows it creates appear here to be checked.
 */

const CAD = /\.(dwg|dxf)$/i;
const DISCIPLINE_ORDER = ["architectural", "structural", "civil", "mechanical", "electrical", "plumbing"];

type Sheet = {
  documentId: string;
  filename: string;
  discipline: string;
  total: number;
  open: number;
  excluded: number;
};

function fmt(v: number | null, unit: string) {
  if (v == null) return "—";
  return `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${unit}`;
}

function shortName(filename: string) {
  return filename.replace(/\.(dwg|dxf|pdf)$/i, "");
}

export default function TakeoffPage({ params }: { params: Promise<{ id: string }> }) {
  const { id: projectId } = use(params);

  const [data, setData] = useState<AnalysisResponse | null>(null);
  const [rows, setRows] = useState<Measurement[] | null>(null);
  const [docs, setDocs] = useState<DocumentRead[]>([]);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState<null | "read" | "sign" | "check">(null);

  const [sheetFilter, setSheetFilter] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [fixing, setFixing] = useState(false);
  const [fixValue, setFixValue] = useState("");
  // null = automatic: open when nothing on the sheet is left to check.
  const [showChecked, setShowChecked] = useState<boolean | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const [a, m, d] = await Promise.all([getTakeoff(projectId), listMeasurements(projectId), listDocuments(projectId)]);
      setData(a);
      setRows(m);
      setDocs(d);
    } catch (e) {
      setLoadError(e);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  // ── derived ────────────────────────────────────────────────────────────
  const docName = useMemo(() => Object.fromEntries(docs.map((d) => [d.id, d.filename])), [docs]);
  const cadFiles = useMemo(() => docs.filter((d) => CAD.test(d.filename)), [docs]);
  const hasCad = cadFiles.length > 0;
  const pack = data?.pack ?? null;
  const grade = data?.gate.grade ?? null;

  const sorted = useMemo(() => {
    const order = (d: string) => {
      const i = DISCIPLINE_ORDER.indexOf(d);
      return i < 0 ? 99 : i;
    };
    return [...(rows ?? [])].sort(
      (a, b) =>
        order(a.discipline) - order(b.discipline) ||
        (docName[a.source_document_id ?? ""] ?? "").localeCompare(docName[b.source_document_id ?? ""] ?? "") ||
        a.quantity_type.localeCompare(b.quantity_type),
    );
  }, [rows, docName]);

  const total = sorted.length;
  const checkedCount = sorted.filter((r) => r.status !== "candidate").length;
  const openCount = total - checkedCount;

  const sheets = useMemo(() => {
    const m = new Map<string, Sheet>();
    for (const r of sorted) {
      const key = r.source_document_id ?? "manual";
      const s = m.get(key) ?? {
        documentId: key,
        filename: r.source_document_id ? docName[r.source_document_id] ?? "Unknown drawing" : "Entered by hand",
        discipline: r.discipline,
        total: 0,
        open: 0,
        excluded: 0,
      };
      s.total += 1;
      if (r.status === "candidate") s.open += 1;
      if (r.status === "excluded") s.excluded += 1;
      m.set(key, s);
    }
    const byDiscipline: Record<string, Sheet[]> = {};
    for (const s of m.values()) (byDiscipline[s.discipline] ??= []).push(s);
    return byDiscipline;
  }, [sorted, docName]);

  // Two files can share a name ("1234 Example - Plan" as .dwg and .dxf); the
  // list showed them identically, so keep the extension where names collide.
  const sheetLabel = useMemo(() => {
    const seen: Record<string, number> = {};
    for (const list of Object.values(sheets)) for (const s of list) seen[shortName(s.filename)] = (seen[shortName(s.filename)] ?? 0) + 1;
    return (filename: string) => (seen[shortName(filename)] > 1 ? filename : shortName(filename));
  }, [sheets]);

  const problemSheets = useMemo(
    () =>
      (pack?.payload.sheets ?? [])
        .filter((s) => s.error || s.skipped || s.not_measured)
        .map((s) => ({
          filename: s.filename,
          reason: s.error ? `Failed: ${s.error}` : s.skipped ? `Skipped: ${s.skipped}` : `Not measured: ${s.not_measured}`,
        })),
    [pack],
  );

  const noDrawings = useMemo(
    () =>
      Object.entries(grade?.disciplines ?? {})
        .filter(([d, v]) => v.source !== "cad" && !sheets[d])
        .map(([d, v]) => ({ discipline: d, source: v.source })),
    [grade, sheets],
  );

  const inView = useMemo(
    () => sorted.filter((r) => !sheetFilter || (r.source_document_id ?? "manual") === sheetFilter),
    [sorted, sheetFilter],
  );
  const queue = inView.filter((r) => r.status === "candidate");
  const checked = inView.filter((r) => r.status !== "candidate");

  // Always something on the drawing pane. It used to open on the first item
  // waiting — on 1234 a PDF measurement, shown as a bare link — and a finished
  // sheet showed "Select a quantity", so the pane read as broken (rollout
  // 2026-10-20). Prefer a CAD item, which draws; fall back to anything in view.
  const current = useMemo(() => {
    const isCad = (r: Measurement) => !!r.source_document_id && CAD.test(docName[r.source_document_id] ?? "");
    return (
      sorted.find((r) => r.id === selectedId) ??
      queue.find(isCad) ??
      queue[0] ??
      inView.find((r) => isCad(r) && r.status !== "excluded") ??
      inView.find(isCad) ??
      inView[0] ??
      null
    );
  }, [sorted, selectedId, queue, inView, docName]);
  // Nothing left to check on this sheet: open the checked list, which is now
  // where the sheet's measurements are.
  const checkedOpen = showChecked ?? queue.length === 0;

  useEffect(() => {
    setFixing(false);
    setFixValue(current?.value != null ? String(current.value) : "");
  }, [current?.id, current?.value]);

  // ── actions ────────────────────────────────────────────────────────────
  const act = useCallback(
    async (action: "confirm" | "correct" | "exclude") => {
      if (!current) return;
      const value = action === "correct" ? Number(fixValue) : undefined;
      if (action === "correct" && (fixValue.trim() === "" || !Number.isFinite(value))) return;
      setBusy("check");
      setActionError(null);
      try {
        const updated = await checkMeasurement(current.id, action, value);
        setRows((rs) => (rs ?? []).map((r) => (r.id === updated.id ? { ...r, ...updated } : r)));
        // On to the next one still waiting, not back to the top of the list.
        const next = queue.find((r) => r.id !== current.id);
        setSelectedId(next?.id ?? null);
        setFixing(false);
        // The gate counts and can_approve depend on this; refresh them quietly.
        getTakeoff(projectId).then(setData).catch(() => {});
      } catch (e) {
        setActionError(e);
      } finally {
        setBusy(null);
      }
    },
    [current, fixValue, queue, projectId],
  );

  const actMany = async (action: "confirm" | "exclude") => {
    const ids = [...picked];
    if (!ids.length) return;
    setBusy("check");
    setActionError(null);
    try {
      await checkMeasurements(projectId, ids, action);
      setPicked(new Set());
      setSelectedId(null);
      await load();
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  };

  const read = async () => {
    if (
      pack?.status === "approved" &&
      !confirm("Reading the drawings again replaces the sign-off. Measurements whose reading changes will need checking again. Go ahead?")
    )
      return;
    setBusy("read");
    setActionError(null);
    try {
      await readDrawings(projectId);
      await load();
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  };

  const sign = async () => {
    setBusy("sign");
    setActionError(null);
    try {
      setData(await signOff(projectId));
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  };

  // Enter = correct · E = fix it · X = don't count. Not while typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (!current || busy || e.metaKey || e.ctrlKey || e.altKey) return;
      // Enter on a focused button or link means that button or link.
      if (e.key === "Enter" && t && (t.tagName === "BUTTON" || t.tagName === "A")) return;
      if (e.key === "Enter") {
        e.preventDefault();
        act("confirm");
      } else if (e.key === "e" || e.key === "E") {
        e.preventDefault();
        setFixing(true);
      } else if (e.key === "x" || e.key === "X") {
        e.preventDefault();
        act("exclude");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, busy, act]);

  if (loadError) return <WorkspaceError error={loadError} />;
  if (!data || !rows) return <p className="text-sm text-slate-500">Loading the takeoff…</p>;

  const signedOff = pack?.status === "approved";
  const feeds = current ? grade?.disciplines?.[current.discipline]?.divisions ?? [] : [];

  return (
    <div className="space-y-4">
      {/* ── the three steps ─────────────────────────────────────────── */}
      <Box className="grid gap-3 p-4 md:grid-cols-3">
        <Step n={1} title="Read the drawings" active={hasCad && !pack}>
          {hasCad ? (
            <>
              <p className="m-0 text-[13px] text-slate-700">
                {cadFiles.length} CAD file{cadFiles.length === 1 ? "" : "s"}
                {pack
                  ? ` · read ${new Date(pack.created_at).toLocaleDateString()} (version ${pack.version_number}), ${pack.payload.sheets_read ?? 0} sheet${pack.payload.sheets_read === 1 ? "" : "s"} measured`
                  : total
                    ? ` · ${total} measurements from an earlier reading; the set itself has not been read`
                    : ", not read yet"}
              </p>
              <button
                type="button"
                onClick={read}
                disabled={busy !== null}
                className={`mt-2 rounded-md px-3 py-1.5 text-sm disabled:opacity-50 ${
                  pack ? "border border-slate-400 text-slate-800" : "bg-slate-900 text-white"
                }`}
              >
                {busy === "read" ? "Reading… this can take a minute or two" : pack ? "Read them again" : "Read them now"}
              </button>
            </>
          ) : (
            <p className="m-0 text-[13px] text-slate-700">
              No CAD files on this project. Measure on PDF drawings under{" "}
              <Link href={`/projects/${projectId}/quantities/architectural`} className="text-blue-700">
                Measure on a PDF
              </Link>
              ; what you measure shows up here to check.
            </p>
          )}
        </Step>

        <Step n={2} title="Check each measurement" active={openCount > 0}>
          <p className="m-0 text-[13px] text-slate-700">
            {total ? `${checkedCount} of ${total} checked` : "Nothing to check yet."}
          </p>
          {total > 0 && (
            <div className="mt-2 h-1.5 rounded bg-slate-200">
              <div className="h-1.5 rounded bg-emerald-600" style={{ width: `${(checkedCount / total) * 100}%` }} />
            </div>
          )}
        </Step>

        <Step n={3} title="Sign off the set" active={!!data.can_approve}>
          {!hasCad ? (
            <p className="m-0 text-[13px] text-slate-700">
              Not needed without CAD files: checking every measurement is enough for the estimate.
            </p>
          ) : signedOff && openCount > 0 ? (
            // A measurement added after sign-off (a PDF length, say) holds the
            // estimate until it is checked; "can use these numbers" was untrue
            // (from-scratch test 2026-10-20).
            <p className="m-0 text-[13px] text-orange-800">
              Signed off {pack?.approved_at ? new Date(pack.approved_at).toLocaleDateString() : ""}, but {openCount}{" "}
              measurement{openCount === 1 ? " was" : "s were"} added since and {openCount === 1 ? "is" : "are"} not checked.
              The estimate waits until {openCount === 1 ? "it is" : "they are"}.
            </p>
          ) : signedOff ? (
            <p className="m-0 text-[13px] text-emerald-800">
              Signed off {pack?.approved_at ? new Date(pack.approved_at).toLocaleDateString() : ""}. The estimate can use these numbers.
            </p>
          ) : (
            <>
              <p className="m-0 text-[13px] text-slate-700">
                Catches a missing floor or a sheet that failed. Lets the estimate use the numbers.
                {!pack ? " Read the drawings first." : openCount ? ` ${openCount} still to check.` : ""}
              </p>
              <button
                type="button"
                onClick={sign}
                disabled={!data.can_approve || busy !== null}
                className="mt-2 rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300 disabled:text-slate-600"
              >
                {busy === "sign" ? "Signing off…" : "Sign off"}
              </button>
            </>
          )}
        </Step>
      </Box>

      {pack?.payload.set_audit?.findings?.length ? (
        // Still true after sign-off, so still shown, but no longer as a to-do
        // (rollout test 2026-10-15: "Before signing off" on a signed-off set).
        <Box className={`${pack.status === "approved" ? "border-slate-300" : "border-orange-700"} p-3 text-[13px]`}>
          {/* Collapsed: six lines of it pushed the drawing below the fold. */}
          <details>
            <summary className="cursor-pointer">
              <b className={pack.status === "approved" ? "text-slate-700" : "text-orange-800"}>
                {pack.status === "approved" ? "Signed off with" : "Before signing off:"}{" "}
                {pack.payload.set_audit.findings.length} gap{pack.payload.set_audit.findings.length === 1 ? "" : "s"} in the drawing set
              </b>
              <span className="ml-2 text-slate-500">show</span>
            </summary>
            <ul className="m-0 mt-1 list-disc pl-5 text-slate-700">
              {pack.payload.set_audit.findings.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </details>
        </Box>
      ) : null}

      {actionError != null && <WorkspaceError error={actionError} />}

      {total === 0 ? (
        <Box className="p-6 text-sm text-slate-600">
          {hasCad
            ? "No measurements yet. Read the drawings to get them."
            : "No measurements yet. Measure on a PDF drawing, or upload the CAD files to Documents."}
        </Box>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)_300px]">
          {/* ── sheets ─────────────────────────────────────────────── */}
          <Box className="h-fit p-3">
            <Label className="mb-2 px-1">Sheets</Label>
            <button
              type="button"
              onClick={() => setSheetFilter(null)}
              className={`flex w-full justify-between rounded px-2 py-1.5 text-left text-[13px] ${!sheetFilter ? "bg-slate-200 font-semibold" : ""}`}
            >
              <span>All sheets</span>
              <span className="text-slate-500">{openCount ? `${openCount} left` : "done"}</span>
            </button>
            {Object.keys(sheets)
              .sort((a, b) => DISCIPLINE_ORDER.indexOf(a) - DISCIPLINE_ORDER.indexOf(b))
              .map((d) => (
                <div key={d} className="mt-2">
                  <div className="px-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{d}</div>
                  {sheets[d].map((s) => (
                    <button
                      key={s.documentId}
                      type="button"
                      onClick={() => {
                        setSheetFilter(s.documentId);
                        setSelectedId(null);
                        setShowChecked(null);
                      }}
                      title={s.filename}
                      className={`flex w-full justify-between gap-2 rounded px-2 py-1.5 text-left text-[13px] ${
                        sheetFilter === s.documentId ? "bg-slate-200 font-semibold" : ""
                      } ${s.excluded === s.total ? "text-slate-400" : ""}`}
                    >
                      <span className="truncate">{sheetLabel(s.filename)}</span>
                      <span className={`shrink-0 ${s.open ? "text-slate-700" : s.excluded === s.total ? "text-slate-400" : "text-emerald-700"}`}>
                        {s.open ? `${s.open}/${s.total}` : s.excluded === s.total ? "not used" : "✓"}
                      </span>
                    </button>
                  ))}
                </div>
              ))}
            {noDrawings.map(({ discipline, source }) => (
              <div key={discipline} className="mt-2">
                <div className="px-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{discipline}</div>
                {source === "pdf" ? (
                  <Link
                    href={`/projects/${projectId}/quantities/${discipline}`}
                    className="block px-2 py-1.5 text-[13px] text-blue-700"
                  >
                    PDF only · measure on the sheet
                  </Link>
                ) : (
                  <div className="px-2 py-1.5 text-[13px] text-orange-800">No drawings · allowance</div>
                )}
              </div>
            ))}
            {problemSheets.length > 0 && (
              <details className="mt-3 border-t border-slate-200 px-2 pt-2 text-xs text-slate-600">
                <summary className="cursor-pointer">Skipped or failed: {problemSheets.length}</summary>
                <ul className="m-0 mt-1 list-none space-y-1.5 p-0">
                  {problemSheets.map((p) => (
                    <li key={p.filename}>
                      <span className="block truncate font-medium text-slate-800" title={p.filename}>
                        {shortName(p.filename)}
                      </span>
                      {p.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </Box>

          {/* ── the drawing ────────────────────────────────────────── */}
          <Box className="min-w-0 p-3">
            {current?.source_document_id && !CAD.test(docName[current.source_document_id] ?? "") ? (
              <PdfPagePane
                documentId={current.source_document_id}
                filename={shortName(docName[current.source_document_id] ?? "PDF")}
                page={current.source_page}
                measureHref={`/projects/${projectId}/quantities/${current.discipline}`}
              />
            ) : (
              <DrawingPane projectId={projectId} q={current} />
            )}
          </Box>

          {/* ── check this one ─────────────────────────────────────── */}
          <div className="space-y-3">
            <Box className="p-4">
              <Label className="mb-2">{current?.status === "candidate" || !current ? "Check this one" : "Already checked"}</Label>
              {!current ? (
                <p className="m-0 text-sm text-emerald-800">
                  {sheetFilter ? "Everything on this sheet is checked." : "Every measurement is checked."}
                </p>
              ) : (
                <div className="space-y-2 rounded-md border-2 border-blue-700 p-3">
                  <div className="text-xs text-slate-600">
                    <span className="capitalize">{current.discipline}</span> ·{" "}
                    {current.source_document_id ? shortName(docName[current.source_document_id] ?? "drawing") : "entered by hand"}
                    {current.source_revision_label ? ` · ${current.source_revision_label}` : ""}
                  </div>
                  <div className="text-base font-semibold">{measurementLabel(current.quantity_type)}</div>
                  <div className="text-2xl font-bold tabular-nums">{fmt(current.value, current.unit)}</div>
                  {current.status === "corrected" && current.machine_value != null && (
                    <div className="text-xs text-slate-600">The machine read {fmt(current.machine_value, current.unit)}.</div>
                  )}
                  {current.status !== "candidate" && (
                    <div className="text-xs text-slate-600">
                      {STATUS_LABEL[current.status]}
                      {current.confirmed_at ? ` · ${new Date(current.confirmed_at).toLocaleDateString()}` : ""}. You can change it.
                    </div>
                  )}
                  <div className="text-xs text-slate-600">
                    {current.source === "cad_extract" ? "Read by the machine from the CAD file." : current.source === "manual_entry" ? "Typed in by a person." : "Measured on the drawing."}
                    {feeds.length ? ` Feeds: ${feeds.join(", ")}.` : ""}
                  </div>
                  {fixing ? (
                    <form
                      className="flex gap-2"
                      onSubmit={(e) => {
                        e.preventDefault();
                        act("correct");
                      }}
                    >
                      <input
                        autoFocus
                        type="number"
                        step="any"
                        value={fixValue}
                        onChange={(e) => setFixValue(e.target.value)}
                        aria-label="Correct value"
                        className="min-w-0 flex-1 rounded border border-slate-400 px-2 py-1 text-right text-sm"
                      />
                      <span className="self-center text-sm text-slate-600">{current.unit}</span>
                      <button type="submit" disabled={busy !== null} className="rounded-md bg-slate-900 px-3 py-1 text-sm text-white disabled:opacity-50">
                        Save
                      </button>
                      <button type="button" onClick={() => setFixing(false)} className="text-sm text-slate-600">
                        Cancel
                      </button>
                    </form>
                  ) : (
                    <div className="grid grid-cols-3 gap-1.5">
                      <button type="button" disabled={busy !== null} onClick={() => act("confirm")} className="rounded-md bg-slate-900 px-2 py-2 text-sm text-white disabled:opacity-50">
                        Correct
                      </button>
                      <button type="button" disabled={busy !== null} onClick={() => setFixing(true)} className="rounded-md border border-slate-900 px-2 py-2 text-sm disabled:opacity-50">
                        Fix it
                      </button>
                      <button type="button" disabled={busy !== null} onClick={() => act("exclude")} className="rounded-md border border-slate-900 px-2 py-2 text-sm disabled:opacity-50">
                        Don&apos;t count
                      </button>
                    </div>
                  )}
                  <div className="text-[11px] text-slate-500">Keys: Enter = correct · E = fix · X = don&apos;t count</div>
                </div>
              )}
            </Box>

            <Box className="p-4">
              <div className="mb-2 flex items-center justify-between">
                <Label>Up next · {queue.length}</Label>
                {queue.length > 1 && (
                  <button
                    type="button"
                    onClick={() => setPicked(picked.size === queue.length ? new Set() : new Set(queue.map((r) => r.id)))}
                    className="text-xs text-blue-700"
                  >
                    {picked.size === queue.length ? "Clear" : "Select all"}
                  </button>
                )}
              </div>
              {picked.size > 0 && (
                <div className="mb-2 flex flex-wrap items-center gap-2 rounded bg-slate-100 p-2 text-xs">
                  <span>{picked.size} selected</span>
                  <button type="button" disabled={busy !== null} onClick={() => actMany("confirm")} className="text-blue-700 disabled:opacity-50">
                    All correct
                  </button>
                  <button type="button" disabled={busy !== null} onClick={() => actMany("exclude")} className="text-blue-700 disabled:opacity-50">
                    Don&apos;t count any
                  </button>
                </div>
              )}
              {queue.length === 0 ? (
                <p className="m-0 text-[13px] text-slate-600">Nothing waiting.</p>
              ) : (
                <ul className="m-0 max-h-80 list-none space-y-1 overflow-y-auto p-0">
                  {queue.map((r) => (
                    <li
                      key={r.id}
                      className={`flex items-center gap-2 rounded border px-2 py-1.5 text-[13px] ${
                        r.id === current?.id ? "border-blue-700" : "border-slate-200"
                      }`}
                    >
                      <input
                        type="checkbox"
                        aria-label={`Select ${measurementLabel(r.quantity_type)}`}
                        checked={picked.has(r.id)}
                        onChange={() =>
                          setPicked((p) => {
                            const n = new Set(p);
                            if (n.has(r.id)) n.delete(r.id);
                            else n.add(r.id);
                            return n;
                          })
                        }
                      />
                      <button type="button" onClick={() => setSelectedId(r.id)} className="flex min-w-0 flex-1 justify-between gap-2 text-left">
                        <span className="truncate">{measurementLabel(r.quantity_type)}</span>
                        <span className="shrink-0 tabular-nums text-slate-600">{fmt(r.value, r.unit)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Box>

            <Box className="p-4">
              <button type="button" onClick={() => setShowChecked(!checkedOpen)} className="flex w-full justify-between text-left">
                <Label>Checked · {checked.length}</Label>
                <span className="text-xs text-blue-700">{checkedOpen ? "Hide" : "Show"}</span>
              </button>
              {checkedOpen && (
                <ul className="m-0 mt-2 max-h-80 list-none space-y-1 overflow-y-auto p-0">
                  {checked.map((r) => (
                    <li key={r.id}>
                      <button
                        type="button"
                        onClick={() => setSelectedId(r.id)}
                        className={`flex w-full justify-between gap-2 rounded border px-2 py-1.5 text-left text-[13px] ${
                          r.id === current?.id ? "border-blue-700" : "border-slate-200"
                        }`}
                      >
                        <span className="truncate">{measurementLabel(r.quantity_type)}</span>
                        <span className={`shrink-0 ${r.status === "excluded" ? "text-slate-500 line-through" : "text-emerald-800"}`}>
                          {fmt(r.value, r.unit)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Box>
          </div>
        </div>
      )}
    </div>
  );
}

function Step({ n, title, active, children }: { n: number; title: string; active: boolean; children: React.ReactNode }) {
  return (
    <div className={`rounded-md p-3 ${active ? "border-2 border-slate-900" : "border border-slate-300"}`}>
      <div className="mb-1 text-sm font-semibold">
        {n} · {title}
      </div>
      {children}
    </div>
  );
}
