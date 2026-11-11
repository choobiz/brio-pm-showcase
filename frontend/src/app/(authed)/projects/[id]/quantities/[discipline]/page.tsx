"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document as PDFDocument, Page as PDFPage, pdfjs } from "react-pdf";

import { Card } from "@/components/ui/card";
import { getDownloadUrl, listDocuments, type DocumentRead } from "@/lib/documents";
import { measurementLabel } from "@/lib/takeoff";
import { CUSTOM_SCALE, scaleSelectValue } from "@/lib/scaleSelect";
import {
  describeLengthError,
  describeScale,
  lengthError,
  namedScaleFor,
  parseFeetInput,
} from "@/lib/scaleCheck";
import { pairRowsWithCandidates } from "@/lib/candidateKey";
import { pathId, refinedStats } from "@/lib/refinedStats";
import {
  candidateKey,
  deleteQuantityOverride,
  deleteScaleOverride,
  getDrawingAnalysis,
  isAnalysisPending,
  listQuantityOverrides,
  listScaleOverrides,
  upsertQuantityOverride,
  upsertScaleOverride,
  NAMED_SCALES,
  type DrawingAnalysis,
  type DrawingPageMeta,
  type LinearCandidate,
  type LinearSummaryRow,
  type PathInCandidate,
  type QuantityOverride,
  type ScaleOverride,
} from "@/lib/drawings";

/**
 * Which kind of drawing a file is, from its name. Documents carry no
 * discipline, and the picker showed every PDF on the project as one row of
 * all-caps chips, so a stormwater set opened first on the Architectural page
 * (rollout 2026-10-20). Order = order in the picker.
 */
const DRAWING_GROUPS: { key: string; label: string; test: RegExp }[] = [
  { key: "schedule", label: "Schedules", test: /schedule/i },
  { key: "structural", label: "Structural", test: /struct|(^|[^a-z])s-?\d/i },
  { key: "civil", label: "Civil and stormwater", test: /smp|storm|civil|grading|survey|servic/i },
  { key: "architectural", label: "Architectural", test: /arch|plan|elev|section|(^|[^a-z])a-?\d/i },
];

function drawingGroup(filename: string): string {
  return DRAWING_GROUPS.find((g) => g.test.test(filename))?.key ?? "other";
}

function prettyName(filename: string): string {
  return filename.replace(/\.pdf$/i, "").replace(/_+/g, " ").replace(/\s+-\s+/g, " – ").trim();
}

/** Where each source came from, in words. */
const SOURCE_LABEL: Record<string, string> = {
  cad_extract: "Read from CAD",
  candidate_confirmed: "Detected length, ticked",
  manual_measure: "Two-point measurement",
  manual_entry: "Typed in",
};

// pdf.js needs an external worker. Configure once per browser.
if (typeof window !== "undefined" && !pdfjs.GlobalWorkerOptions.workerSrc) {
  pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;
}

// Generic fallback labels when the drawing's discipline can't be detected.
// Honest about being geometry-derived — these surface only when the
// per-discipline lookup below has no entry.
const CLASS_LABEL: Record<string, string> = {
  wall_filled: "Solid-filled shape (centre line)",
  wall_or_partition: "Thick orthogonal stroke",
  border_or_outline: "Sheet outline / border",
  dimension_line: "Thin orthogonal stroke (dimension)",
  hatch_or_detail: "Fine hatching / detail",
  general_lines_ortho: "Medium orthogonal stroke",
  general_lines_curve: "Curve / diagonal",
  other: "Other stroke",
};

const CLASS_HINT: Record<string, string> = {
  wall_filled: "A run drawn as a solid fill, measured along its centre line (area ÷ drawn thickness). Click it to check before ticking.",
  wall_or_partition: "~1.0–2.0pt weight, orthogonal. On a generic sheet this could be a wall, equipment outline, or major run.",
  border_or_outline: "Likely the sheet-border or title-block ring.",
  dimension_line: "Thin orthogonal — usually drafting markup, not a billable element.",
  hatch_or_detail: "Repetitive thin strokes — wall-fill or shading.",
  general_lines_ortho: "Medium-weight orthogonal — varies by trade.",
  general_lines_curve: "Curves / diagonals — varies by trade.",
  other: "Didn't fit any bucket cleanly.",
};

/** Trade-aware labels: same geometric class means different things on
 * different trade drawings. Detected from the drawing's filename in
 * `detectDrawingDiscipline()`. Falls back to CLASS_LABEL/CLASS_HINT when
 * the discipline can't be inferred. */
const DISCIPLINE_LABELS: Record<string, Record<string, { label: string; hint: string }>> = {
  architectural: {
    // Walls drawn as solid grey/black fills (456 W Sample ID-1/ID-2): the
    // only walls on that set, and never listed before 2026-10-26.
    wall_filled: {
      label: "Wall (solid fill), centre line",
      hint: "A wall drawn as a solid fill, measured along its centre line (its area divided by its drawn thickness). Joined walls are one row. Click it to check before ticking.",
    },
    wall_or_partition: {
      label: "Likely a wall",
      hint: "Thick straight lines on an architectural drawing are usually walls. Click it to check before ticking.",
    },
    dimension_line: {
      label: "Dimension callout",
      hint: "Architectural dimension reference — drafting markup, not billable scope.",
    },
    general_lines_ortho: {
      // Was "Cabinetry, casework or millwork outline": on 456 W Sample it
      // named the elevator cabs. The weight alone cannot tell; say so.
      label: "Medium straight lines",
      hint: "Could be millwork, doors, fixtures, elevators or stairs. Click it to see the lines before ticking.",
    },
    general_lines_curve: {
      label: "Door swing or curve",
      hint: "Curves on arch drawings are typically door arcs, radius details, or decorative elements.",
    },
    other: { label: "Other architectural element", hint: "" },
  },
  electrical: {
    wall_or_partition: {
      label: "Conduit / feeder run",
      hint: "Thick orthogonal strokes on an electrical drawing are typically major conduit feeders, panel-to-equipment runs, or service entrances.",
    },
    dimension_line: {
      label: "Dimension callout",
      hint: "Dimension reference for fixture placement — not billable scope on its own.",
    },
    general_lines_ortho: {
      label: "Branch wiring run",
      hint: "Medium-weight orthogonal lines on electrical drawings are typically branch circuits and homeruns.",
    },
    general_lines_curve: {
      label: "Low-voltage or data run",
      hint: "Curves on electrical drawings often represent low-voltage, fire alarm, or data cabling that doesn't follow strict orthogonal paths.",
    },
    other: { label: "Other electrical element", hint: "" },
  },
  mechanical: {
    wall_or_partition: {
      label: "Ductwork run",
      hint: "Thick orthogonal strokes on a mechanical/HVAC drawing are typically duct trunk lines or major equipment connections.",
    },
    dimension_line: {
      label: "Dimension callout",
      hint: "Dimension reference for equipment placement.",
    },
    general_lines_ortho: {
      label: "Branch duct or equipment connection",
      hint: "Medium-weight orthogonal on a mech drawing — typically branch ducts or equipment-to-trunk connections.",
    },
    general_lines_curve: {
      label: "Refrigerant or condensate piping",
      hint: "Curves on a mechanical drawing are typically refrigerant lines, condensate drains, or flex duct.",
    },
    other: { label: "Other mechanical element", hint: "" },
  },
  plumbing: {
    wall_or_partition: {
      label: "Pipe run (supply or DWV)",
      hint: "Thick orthogonal strokes on a plumbing drawing are typically main supply or DWV piping runs.",
    },
    dimension_line: {
      label: "Dimension callout",
      hint: "Dimension reference for fixture placement.",
    },
    general_lines_ortho: {
      label: "Branch piping or fixture connection",
      hint: "Medium-weight orthogonal — typically branch supply, vent, or fixture connection runs.",
    },
    general_lines_curve: {
      label: "Curved pipe or fitting detail",
      hint: "Curves on plumbing drawings are typically fittings, P-traps, or curved supply runs.",
    },
    other: { label: "Other plumbing element", hint: "" },
  },
  structural: {
    wall_or_partition: {
      label: "Structural member outline",
      hint: "Thick orthogonal strokes on a structural drawing are typically beam, column, or shear-wall outlines.",
    },
    dimension_line: {
      label: "Dimension callout",
      hint: "Structural dimension reference.",
    },
    general_lines_ortho: {
      label: "Framing or rebar layout",
      hint: "Medium-weight orthogonal — typically framing layouts or rebar placement.",
    },
    general_lines_curve: {
      label: "Reinforcement or detail",
      hint: "Curves on structural drawings — typically reinforcement bends, brackets, or connection details.",
    },
    other: { label: "Other structural element", hint: "" },
  },
};

/** Infer the drawing's trade discipline from its filename. */
function detectDrawingDiscipline(filename: string | undefined): string | null {
  if (!filename) return null;
  const f = filename.toLowerCase();
  // Order matters: check more-specific terms first to avoid partial matches.
  if (f.includes("struct")) return "structural";
  if (f.includes("mech") || f.includes("hvac")) return "mechanical";
  if (f.includes("plumb")) return "plumbing";
  // "electrical" — also catches the common "electrcial" typo we have on PROJB.
  if (f.includes("electr") || f.includes("elec ") || f.includes("elec-")) return "electrical";
  if (f.includes("arch")) return "architectural";
  return null;
}

/** Resolve the trade-aware label for a candidate classification, falling
 * back to the generic geometric label when the drawing discipline is
 * unknown. */
function labelFor(classification: string, drawingDisc: string | null): string {
  if (drawingDisc && DISCIPLINE_LABELS[drawingDisc]?.[classification]) {
    return DISCIPLINE_LABELS[drawingDisc][classification].label;
  }
  return CLASS_LABEL[classification] ?? classification;
}

function hintFor(classification: string, drawingDisc: string | null): string {
  if (drawingDisc && DISCIPLINE_LABELS[drawingDisc]?.[classification]) {
    return DISCIPLINE_LABELS[drawingDisc][classification].hint;
  }
  return CLASS_HINT[classification] ?? "";
}

// Page width at 100% zoom. The zoom bar multiplies it and the page is redrawn
// at that size, so text stays sharp (browser zoom only magnified a 760 px
// image: a 36" sheet's scale note could not be read, 2026-10-26). Everything
// stored is in PDF units, so zoom never changes a measurement.
const BASE_RENDER_WIDTH = 760;
const ZOOMS = [1, 1.5, 2, 3, 4, 6];
// Keep each page canvas under ~16M device pixels: Safari/iPad draws nothing
// above that, and every page at 6x would otherwise hold ~100 MB.
const MAX_CANVAS_PIXELS = 16_000_000;

function devicePixelRatioFor(cssWidth: number, cssHeight: number): number {
  const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
  return Math.max(0.5, Math.min(dpr, Math.sqrt(MAX_CANVAS_PIXELS / Math.max(1, cssWidth * cssHeight))));
}

/** Draws its child only while near the visible part of the drawing pane, so a
 * zoomed 18-page set does not keep 18 large canvases in memory. */
function NearViewport({
  root,
  width,
  height,
  children,
}: {
  root: HTMLElement | null;
  width: number;
  height: number;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setNear(true); // no observer: just draw it
      return;
    }
    const io = new IntersectionObserver(([e]) => setNear(e.isIntersecting), {
      root,
      rootMargin: "100% 0px",
    });
    io.observe(el);
    return () => io.disconnect();
  }, [root]);
  return (
    <div ref={ref} style={{ width, minHeight: near ? undefined : height }}>
      {near ? children : null}
    </div>
  );
}

/** Transform a PDF bbox into screen-pixel coords inside the rendered page,
 * accounting for page rotation. pdf.js rotates the canvas using `page.rotation`
 * so the visible long-axis matches what a normal viewer shows; PyMuPDF's
 * bboxes are in pre-rotation PDF space, so we rotate them to match. */
function bboxToScreen(
  bbox: [number, number, number, number],
  page: DrawingPageMeta,
  renderWidth: number,
): { left: number; top: number; width: number; height: number } | null {
  const [x0, y0, x1, y1] = bbox;
  // PyMuPDF's page.rect returns POST-rotation dimensions, but get_drawings()
  // returns paths in pre-rotation coords. Recover the pre-rotation
  // dimensions before applying the rotation transform.
  const isRotated = page.rotation % 180 === 90;
  const Wpre = isRotated ? page.height : page.width;
  const Hpre = isRotated ? page.width : page.height;
  if (Wpre <= 0 || Hpre <= 0) return null;

  let sx0: number, sy0: number, sx1: number, sy1: number;
  switch (((page.rotation % 360) + 360) % 360) {
    case 90: // displayed rotated 90° CW: (x, y) -> (Hpre - y, x)
      sx0 = Hpre - y1; sy0 = x0; sx1 = Hpre - y0; sy1 = x1; break;
    case 180:
      sx0 = Wpre - x1; sy0 = Hpre - y1; sx1 = Wpre - x0; sy1 = Hpre - y0; break;
    case 270: // displayed rotated 90° CCW (PROJB + Maple are 270): (x, y) -> (y, Wpre - x)
      sx0 = y0; sy0 = Wpre - x1; sx1 = y1; sy1 = Wpre - x0; break;
    default:
      sx0 = x0; sy0 = y0; sx1 = x1; sy1 = y1; break;
  }
  // page.width is already the post-rotation width; that's what we render to.
  const scale = renderWidth / page.width;
  return {
    left: Math.min(sx0, sx1) * scale,
    top: Math.min(sy0, sy1) * scale,
    width: Math.abs(sx1 - sx0) * scale,
    height: Math.abs(sy1 - sy0) * scale,
  };
}

/** Parse a confirmed override's `notes` field into the user's exclusion set. */
function parseExclusions(notes: string | null): Set<string> {
  if (!notes) return new Set();
  try {
    const j = JSON.parse(notes);
    if (j && Array.isArray(j.excluded_paths)) {
      return new Set(j.excluded_paths.map(String));
    }
  } catch {
    /* legacy / non-JSON notes — treat as no exclusions */
  }
  return new Set();
}

/** Parse a saved manual-measurement override's notes into its endpoint
 * coords (post-rotation PDF space).  */
function parseManualLine(notes: string | null): {
  start: [number, number]; end: [number, number]; description?: string;
} | null {
  if (!notes) return null;
  try {
    const j = JSON.parse(notes);
    if (Array.isArray(j.start) && Array.isArray(j.end)) {
      return {
        start: [Number(j.start[0]), Number(j.start[1])],
        end: [Number(j.end[0]), Number(j.end[1])],
        description: typeof j.description === "string" ? j.description : undefined,
      };
    }
  } catch { /* legacy notes */ }
  return null;
}

// Title-block / page-border heuristic. A candidate whose bbox spans nearly
// the whole sheet is the drawing-sheet border, not a measurable element.
function isPageFrame(
  cand: LinearCandidate,
  page: DrawingPageMeta | undefined,
): boolean {
  if (!page || page.width <= 0 || page.height <= 0) return false;
  const [x0, y0, x1, y1] = cand.bbox;
  const w = Math.abs(x1 - x0);
  const h = Math.abs(y1 - y0);
  // Either dimension covering > 90% of the page is a giveaway it's the
  // sheet border; both dimensions > 75% catches the title-block frame.
  const wFrac = w / page.width;
  const hFrac = h / page.height;
  if (wFrac > 0.9 && hFrac > 0.9) return true;
  if (wFrac > 0.75 && hFrac > 0.75 && cand.path_count < 30) return true;
  return false;
}

interface PageProps {
  params: Promise<{ id: string; discipline: string }>;
}

export default function QuantitiesReviewPage({ params }: PageProps) {
  const { id: projectId, discipline } = use(params);

  const [docs, setDocs] = useState<DocumentRead[]>([]);
  // Every document, CAD included, to name the source of a saved reading.
  const [allDocs, setAllDocs] = useState<DocumentRead[]>([]);
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<DrawingAnalysis | null>(null);
  const [overrides, setOverrides] = useState<QuantityOverride[]>([]);
  const [scaleOverrides, setScaleOverrides] = useState<ScaleOverride[]>([]);
  const [busy, setBusy] = useState(false);
  // Drawing analysis is prepared in the background; the page polls.
  const [analysisState, setAnalysisState] = useState<"loading" | "analysing" | "failed" | "ready">("loading");
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analysisRetry, setAnalysisRetry] = useState(0);
  const [zoom, setZoom] = useState(1);
  const renderWidth = BASE_RENDER_WIDTH * zoom;
  const [pane, setPane] = useState<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [numPages, setNumPages] = useState<number>(0);
  const [activeCandidateKey, setActiveCandidateKey] = useState<string | null>(null);
  // Per-candidate set of stroke IDs the user has excluded. Seeded from
  // existing confirmed overrides on load; mutated by clicking red boxes on
  // the PDF; persisted when ✓ is clicked.
  const [pathExclusions, setPathExclusions] = useState<Record<string, Set<string>>>({});
  // Manual line-drop measurement state.
  const [measureMode, setMeasureMode] = useState(false);
  const [firstPoint, setFirstPoint] = useState<
    { page: number; postX: number; postY: number } | null
  >(null);
  const [pendingMeasure, setPendingMeasure] = useState<
    {
      page: number;
      start: [number, number];   // post-rotation PDF coords
      end: [number, number];
      realFeet: number;
    } | null
  >(null);
  const [measureType, setMeasureType] = useState<string>("wall_or_partition");
  const [measureDescription, setMeasureDescription] = useState<string>("");
  // Two-point scale calibration state. Active for a single page at a time;
  // first/second click captures a pixel distance the user labels with a real
  // length, which yields pdf_units_per_real_foot for that page.
  const [calibrate, setCalibrate] = useState<{
    page: number;
    first: { postX: number; postY: number } | null;
    distPdf: number | null;
    end: { postX: number; postY: number } | null;
  } | null>(null);
  const [calibrateFeet, setCalibrateFeet] = useState<string>("");

  const enterMeasureMode = useCallback(() => {
    setActiveCandidateKey(null);  // clear active candidate when measuring
    setCalibrate(null);           // mutually exclusive with calibrate mode
    setFirstPoint(null);
    setPendingMeasure(null);
    setMeasureMode(true);
  }, []);

  const cancelMeasure = useCallback(() => {
    setMeasureMode(false);
    setFirstPoint(null);
    setPendingMeasure(null);
    setMeasureDescription("");
  }, []);

  const togglePathExclusion = useCallback((candKey: string, pid: string) => {
    setPathExclusions((prev) => {
      const next = { ...prev };
      const set = new Set(next[candKey] ?? []);
      if (set.has(pid)) set.delete(pid);
      else set.add(pid);
      next[candKey] = set;
      return next;
    });
  }, []);


  useEffect(() => {
    listDocuments(projectId)
      .then((rows) => {
        setAllDocs(rows);
        const pdfs = rows.filter(
          (d) => d.category === "drawing" && (d.mime ?? "").includes("pdf"),
        );
        // The same file uploaded twice listed twice; keep the newest copy.
        const newest = new Map<string, DocumentRead>();
        for (const d of pdfs) {
          const k = d.filename.toLowerCase();
          const prev = newest.get(k);
          if (!prev || d.created_at > prev.created_at) newest.set(k, d);
        }
        const drawings = [...newest.values()].sort((a, b) => a.filename.localeCompare(b.filename));
        setDocs(drawings);
        // Open on a drawing of THIS page's discipline, not whatever sorts first.
        const own = drawings.filter((d) => drawingGroup(d.filename) === discipline);
        const first = own.find((d) => /arch/i.test(d.filename)) ?? own[0] ?? drawings[0];
        if (first) setSelectedDoc((s) => s ?? first.id);
      })
      .catch((e) => setError(String(e)));
  }, [projectId, discipline]);

  useEffect(() => {
    if (!selectedDoc) return;
    setBusy(true);
    setAnalysis(null);
    setPdfUrl(null);
    setNumPages(0);
    // The drawing itself does not wait for its analysis: that is prepared in
    // the background and polled for below.
    Promise.all([
      getDownloadUrl(selectedDoc),
      listQuantityOverrides(projectId, discipline),
      listScaleOverrides(selectedDoc),
    ])
      .then(([dl, ovs, scaleOvs]) => {
        setPdfUrl(dl.download_url);
        setOverrides(ovs);
        setScaleOverrides(scaleOvs);
        // Seed per-candidate exclusions from existing overrides so the
        // user's previous refinements pre-paint correctly.
        const seed: Record<string, Set<string>> = {};
        for (const o of ovs) {
          if (
            o.source === "candidate_confirmed" &&
            o.source_document_id === selectedDoc &&
            o.source_candidate_key
          ) {
            seed[o.source_candidate_key] = parseExclusions(o.notes);
          }
        }
        setPathExclusions(seed);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(false));
  }, [selectedDoc, projectId, discipline]);

  // Poll the analysis while the background worker prepares it.
  useEffect(() => {
    if (!selectedDoc) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setAnalysisState("loading");
    setAnalysisError(null);
    const tick = (force: boolean, tries: number) => {
      getDrawingAnalysis(selectedDoc, force)
        .then((r) => {
          if (stop) return;
          if (isAnalysisPending(r)) {
            if (r.analysis_status === "failed") {
              setAnalysisState("failed");
              setAnalysisError(r.error ?? "The drawing could not be analysed.");
              return;
            }
            setAnalysisState("analysing");
            // About a minute at 3 s, then every 10 s: a queue behind a long
            // estimate run can take several minutes.
            timer = setTimeout(() => tick(false, tries + 1), tries < 20 ? 3000 : 10000);
            return;
          }
          setAnalysis(r);
          setAnalysisState("ready");
        })
        .catch((e) => {
          if (stop) return;
          setAnalysisState("failed");
          setAnalysisError(String(e));
        });
    };
    tick(analysisRetry > 0, 0);
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [selectedDoc, analysisRetry]);

  // Zoom keeps the point under the pane's centre in place.
  const applyZoom = useCallback(
    (next: number) => {
      const z = Math.min(ZOOMS[ZOOMS.length - 1], Math.max(ZOOMS[0], next));
      if (pane) {
        const cx = (pane.scrollLeft + pane.clientWidth / 2) / zoom;
        const cy = (pane.scrollTop + pane.clientHeight / 2) / zoom;
        requestAnimationFrame(() => {
          pane.scrollLeft = cx * z - pane.clientWidth / 2;
          pane.scrollTop = cy * z - pane.clientHeight / 2;
        });
      }
      setZoom(z);
    },
    [pane, zoom],
  );
  const stepZoom = useCallback(
    (dir: 1 | -1) => {
      const i = ZOOMS.findIndex((z) => z >= zoom - 1e-9);
      applyZoom(ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, (i < 0 ? 0 : i) + dir))]);
    },
    [zoom, applyZoom],
  );
  // Trackpad pinch (ctrl+wheel) inside the pane zooms the drawing, not the page.
  useEffect(() => {
    if (!pane) return;
    // A pinch fires many small events; one zoom step per ~60 px of travel.
    let travel = 0;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      travel += e.deltaY;
      if (Math.abs(travel) < 60) return;
      stepZoom(travel < 0 ? 1 : -1);
      travel = 0;
    };
    pane.addEventListener("wheel", onWheel, { passive: false });
    return () => pane.removeEventListener("wheel", onWheel);
  }, [pane, stepZoom]);

  // Effective per-page scale: a manual/calibrated override (when present)
  // takes priority over the auto-detected scale. Everything downstream
  // (candidate real-feet, manual measure, calibration) reads from here.
  const scaleByPage = useMemo(() => {
    const m = new Map<
      number,
      { ppf: number | null; label: string | null; source: string }
    >();
    if (analysis) {
      for (const s of analysis.scales) {
        m.set(s.page, {
          ppf: s.pdf_units_per_real_foot,
          label: s.scale_label,
          source: s.pdf_units_per_real_foot ? "auto" : "none",
        });
      }
    }
    for (const o of scaleOverrides) {
      m.set(o.page, {
        ppf: o.pdf_units_per_real_foot,
        label: o.scale_label,
        source: o.source,
      });
    }
    return m;
  }, [analysis, scaleOverrides]);

  const ppfFor = useCallback(
    (page: number): number | null => scaleByPage.get(page)?.ppf ?? null,
    [scaleByPage],
  );

  // Pick a named scale from the dropdown — backend derives the conversion.
  const setNamedScale = useCallback(
    async (page: number, label: string) => {
      if (!selectedDoc || !label) return;
      try {
        const saved = await upsertScaleOverride(selectedDoc, page, {
          scale_label: label,
          source: "manual_select",
        });
        setScaleOverrides((prev) => [
          ...prev.filter((o) => o.page !== page),
          saved,
        ]);
      } catch (e) {
        setError(String(e));
      }
    },
    [selectedDoc],
  );

  const clearScale = useCallback(
    async (page: number) => {
      if (!selectedDoc) return;
      try {
        await deleteScaleOverride(selectedDoc, page);
        setScaleOverrides((prev) => prev.filter((o) => o.page !== page));
      } catch (e) {
        setError(String(e));
      }
    },
    [selectedDoc],
  );

  const startCalibrate = useCallback((page: number) => {
    setMeasureMode(false);
    setActiveCandidateKey(null);
    setCalibrateFeet("");
    setCalibrate({ page, first: null, distPdf: null, end: null });
  }, []);

  const cancelCalibrate = useCallback(() => {
    setCalibrate(null);
    setCalibrateFeet("");
  }, []);

  const handleCalibrateClick = useCallback(
    (pageNum: number, e: React.MouseEvent<HTMLDivElement>) => {
      if (!analysis || !calibrate || calibrate.page !== pageNum) return;
      const meta = analysis.pages.find((p) => p.page === pageNum);
      if (!meta) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const scale = renderWidth / meta.width;
      const postX = (e.clientX - rect.left) / scale;
      const postY = (e.clientY - rect.top) / scale;
      if (!calibrate.first) {
        setCalibrate({ ...calibrate, first: { postX, postY }, distPdf: null, end: null });
      } else {
        const dx = postX - calibrate.first.postX;
        const dy = postY - calibrate.first.postY;
        const distPdf = Math.sqrt(dx * dx + dy * dy);
        setCalibrate({ ...calibrate, distPdf, end: { postX, postY } });
      }
    },
    [analysis, calibrate, renderWidth],
  );

  const saveCalibration = useCallback(async () => {
    if (!selectedDoc || !calibrate?.distPdf) return;
    const feet = parseFeetInput(calibrateFeet);
    if (feet === null) {
      setError(`Enter the known length in feet, e.g. 48.42 or 48'-5".`);
      return;
    }
    const ppf = calibrate.distPdf / feet;
    try {
      const saved = await upsertScaleOverride(selectedDoc, calibrate.page, {
        pdf_units_per_real_foot: ppf,
        scale_label: `calibrated (${feet.toFixed(2)} ft ref)`,
        source: "calibrated",
      });
      setScaleOverrides((prev) => [
        ...prev.filter((o) => o.page !== calibrate.page),
        saved,
      ]);
      setCalibrate(null);
      setCalibrateFeet("");
    } catch (e) {
      setError(String(e));
    }
  }, [selectedDoc, calibrate, calibrateFeet]);

  // Use the scale the sheet's printed dimensions measure at (scale_check.py).
  const applyMeasuredScale = useCallback(
    async (page: number, ppf: number, agreeing: number, like: string | null) => {
      if (!selectedDoc) return;
      const named = namedScaleFor(ppf, like);
      try {
        const saved = named
          ? await upsertScaleOverride(selectedDoc, page, { scale_label: named, source: "manual_select" })
          : await upsertScaleOverride(selectedDoc, page, {
              pdf_units_per_real_foot: ppf,
              scale_label: `measured (${agreeing} printed dimensions)`,
              source: "calibrated",
            });
        setScaleOverrides((prev) => [...prev.filter((o) => o.page !== page), saved]);
      } catch (e) {
        setError(String(e));
      }
    },
    [selectedDoc],
  );

  const confirmedByKey = useMemo(() => {
    const m = new Map<string, QuantityOverride>();
    for (const o of overrides) {
      if (
        o.source === "candidate_confirmed" &&
        o.source_candidate_key &&
        o.source_document_id === selectedDoc
      ) {
        m.set(o.source_candidate_key, o);
      }
    }
    return m;
  }, [overrides, selectedDoc]);

  const handleMeasureClick = useCallback(
    (pageNum: number, e: React.MouseEvent<HTMLDivElement>) => {
      if (!analysis) return;
      const meta = analysis.pages.find((p) => p.page === pageNum);
      const ppf = ppfFor(pageNum);
      if (!meta || !ppf) {
        setError("This page has no scale yet — set or calibrate the page scale first (manual measurement needs one).");
        return;
      }
      const rect = e.currentTarget.getBoundingClientRect();
      const screenX = e.clientX - rect.left;
      const screenY = e.clientY - rect.top;
      const scale = renderWidth / meta.width;
      const postX = screenX / scale;
      const postY = screenY / scale;

      if (!firstPoint || firstPoint.page !== pageNum) {
        setFirstPoint({ page: pageNum, postX, postY });
        setPendingMeasure(null);
      } else {
        const dx = postX - firstPoint.postX;
        const dy = postY - firstPoint.postY;
        const distPdf = Math.sqrt(dx * dx + dy * dy);
        const realFeet = distPdf / ppf;
        setPendingMeasure({
          page: pageNum,
          start: [firstPoint.postX, firstPoint.postY],
          end: [postX, postY],
          realFeet,
        });
        setFirstPoint(null);
      }
    },
    [analysis, firstPoint, ppfFor, renderWidth],
  );

  const saveMeasure = useCallback(async () => {
    if (!pendingMeasure || !selectedDoc) return;
    try {
      const notes = JSON.stringify({
        start: pendingMeasure.start,
        end: pendingMeasure.end,
        description: measureDescription || undefined,
      });
      const created = await upsertQuantityOverride({
        project_id: projectId,
        discipline,
        quantity_type: measureType,
        value: pendingMeasure.realFeet,
        unit: "ft",
        source: "manual_measure",
        source_document_id: selectedDoc,
        source_page: pendingMeasure.page,
        source_candidate_key: null,
        notes,
      });
      setOverrides((prev) => [...prev, created]);
      setPendingMeasure(null);
      setMeasureDescription("");
      // Stay in measure mode so user can drop more lines without re-toggling.
    } catch (e) {
      setError(String(e));
    }
  }, [pendingMeasure, selectedDoc, projectId, discipline, measureType, measureDescription]);

  const deleteMeasure = useCallback(
    async (id: string) => {
      try {
        await deleteQuantityOverride(id);
        setOverrides((prev) => prev.filter((o) => o.id !== id));
      } catch (e) {
        setError(String(e));
      }
    },
    [],
  );

  const toggleCandidate = useCallback(
    async (cand: LinearCandidate, pdfUnitsPerFoot: number | null | undefined) => {
      if (!selectedDoc) return;
      const key = candidateKey(cand);
      const existing = confirmedByKey.get(key);
      try {
        if (existing) {
          // Un-confirm — drop the override AND clear local exclusions for it.
          await deleteQuantityOverride(existing.id);
          setOverrides((prev) => prev.filter((o) => o.id !== existing.id));
          setPathExclusions((prev) => {
            const next = { ...prev };
            delete next[key];
            return next;
          });
        } else {
          // Confirm — compute the refined value from the current exclusion
          // set and save the excluded path-ids in notes so we can re-render
          // the user's refinement state next visit.
          const excluded = pathExclusions[key] ?? new Set<string>();
          const stats = refinedStats(cand, pdfUnitsPerFoot, excluded);
          const notes = excluded.size > 0
            ? JSON.stringify({ excluded_paths: [...excluded] })
            : null;
          const created = await upsertQuantityOverride({
            project_id: projectId,
            discipline,
            quantity_type: cand.classification,
            value: stats.realFeet,
            unit: "ft",
            source: "candidate_confirmed",
            source_document_id: selectedDoc,
            source_page: cand.page,
            source_candidate_key: key,
            notes,
          });
          setOverrides((prev) => [...prev, created]);
        }
      } catch (e) {
        setError(String(e));
      }
    },
    [confirmedByKey, projectId, discipline, selectedDoc, pathExclusions],
  );

  // Detect the drawing's own discipline from its filename so we can apply
  // trade-aware labels (e.g. "Conduit / feeder run" on the Electrical
  // drawing instead of the generic "thick orthogonal stroke").
  const drawingDiscipline = useMemo(() => {
    const doc = docs.find((d) => d.id === selectedDoc);
    return detectDrawingDiscipline(doc?.filename);
  }, [docs, selectedDoc]);

  const rankedRows = useMemo(() => {
    if (!analysis) return [];
    const pagesByNum = new Map<number, DrawingPageMeta>(
      analysis.pages.map((p) => [p.page, p]),
    );

    // Classification priority — same LF figure means more in
    // `wall_or_partition` than in `other`.
    const CLASS_PRIORITY: Record<string, number> = {
      wall_or_partition: 1,
      dimension_line: 2,
      general_lines_ortho: 3,
      general_lines_curve: 4,
      other: 5,
    };

    const rows: { row: LinearSummaryRow; candidate: LinearCandidate }[] = [];
    // Each row is paired with ITS OWN group. It used to take the first group of
    // the same page/type/weight, so every "13 ft" row highlighted and saved the
    // same group (456 W Sample, 2026-10-26).
    const own = pairRowsWithCandidates(analysis.linear_summary, analysis.candidates);
    for (let i = 0; i < analysis.linear_summary.length; i++) {
      const r = analysis.linear_summary[i];
      // Compute real feet from the EFFECTIVE page scale (manual/calibrated
      // override wins over auto-detect), not the stale server value baked in
      // at parse time — that's what makes a just-set scale surface candidates.
      const ppf = scaleByPage.get(r.page)?.ppf ?? null;
      const realFeet = ppf && ppf > 0 ? r.pdf_units / ppf : null;
      if (realFeet === null || realFeet < 5) continue;
      if (r.classification === "hatch_or_detail") continue;
      if (r.classification === "border_or_outline") continue;
      // No more cross-discipline filtering — each drawing's candidates are
      // labelled per its own discipline (see labelFor/hintFor). The user
      // navigates between drawings via the selector buttons; nothing is
      // silently hidden based on URL.
      // Drop suspiciously-large catch-all groups that are almost certainly
      // aggregates of unrelated lines — typically the "other (thick)" or
      // "general_lines_*" with thousands of paths summed across a sheet.
      if (
        (r.classification === "other" ||
          r.classification === "general_lines_curve") &&
        r.path_count > 800
      ) {
        continue;
      }

      const cand = own[i];
      if (!cand) continue;
      if (isPageFrame(cand, pagesByNum.get(r.page))) continue;

      rows.push({ row: r, candidate: cand });
    }
    // Within a page, sort by classification priority then real_feet desc.
    // (Page-level ordering happens in the render below — we group by page.)
    rows.sort((a, b) => {
      if (a.row.page !== b.row.page) return a.row.page - b.row.page;
      const pa = CLASS_PRIORITY[a.row.classification] ?? 99;
      const pb = CLASS_PRIORITY[b.row.classification] ?? 99;
      if (pa !== pb) return pa - pb;
      return (b.row.real_feet ?? 0) - (a.row.real_feet ?? 0);
    });
    return rows;  // no cap — grouped by page in the render
  }, [analysis, discipline, scaleByPage]);

  const rowsByPage = useMemo(() => {
    const m = new Map<number, typeof rankedRows>();
    for (const r of rankedRows) {
      const list = m.get(r.row.page) ?? [];
      list.push(r);
      m.set(r.row.page, list);
    }
    return m;
  }, [rankedRows]);

  const scaledPages = [...scaleByPage.values()].filter((v) => v.ppf).length;
  // One row per reading: identical CAD values from the .dwg and .dxf of the
  // same plan collapse into one, noting the copies.
  const savedRows = (() => {
    const out: { o: QuantityOverride; copies: number }[] = [];
    const byKey = new Map<string, { o: QuantityOverride; copies: number }>();
    for (const o of overrides.filter((x) => x.discipline === discipline)) {
      const k = o.source === "cad_extract" ? `${o.quantity_type}|${o.value}|${o.unit}` : o.id;
      const hit = byKey.get(k);
      if (hit) hit.copies += 1;
      else {
        const row = { o, copies: 1 };
        byKey.set(k, row);
        out.push(row);
      }
    }
    return out;
  })();
  // What this page itself produced: CAD readings belong to the Takeoff tab.
  const savedForDiscipline = overrides.filter(
    (o) => o.discipline === discipline && o.source !== "cad_extract",
  ).length;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>
          Measure on a PDF · <span className="capitalize">{discipline}</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          For drawings you only have as PDF. Lengths saved here go to the Takeoff tab, where they are checked before the
          estimate uses them.
        </p>
        {/* What you save is filed under this discipline, so it is chosen here,
            not inferred from whichever drawing happens to be open. */}
        <div className="mt-2 flex flex-wrap gap-1 text-xs">
          {["architectural", "structural", "civil", "mechanical", "electrical", "plumbing"].map((d) => (
            <Link
              key={d}
              href={`/projects/${projectId}/quantities/${d}`}
              className={`rounded border px-2 py-1 capitalize ${
                d === discipline ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 text-slate-700 hover:bg-slate-50"
              }`}
            >
              {d}
            </Link>
          ))}
        </div>
      </div>

      {/* The three steps, with where you are in each. Replaces a six-point
          essay that described the geometry as "the AI" (it is not one). */}
      <Card className="grid gap-3 p-4 md:grid-cols-3">
        <StepBox n={1} title="Pick a drawing" done={!!selectedDoc}>
          {docs.length === 0 ? (
            "No PDF drawings on this project. Upload them under Documents."
          ) : (
            <select
              value={selectedDoc ?? ""}
              onChange={(e) => setSelectedDoc(e.target.value || null)}
              className="mt-1 w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-sm"
              aria-label="Drawing"
            >
              {[...DRAWING_GROUPS.map((g) => g.key), "other"]
                .map((key) => ({
                  key,
                  label: DRAWING_GROUPS.find((g) => g.key === key)?.label ?? "Other",
                  items: docs.filter((d) => drawingGroup(d.filename) === key),
                }))
                .filter((g) => g.items.length > 0)
                .map((g) => (
                  <optgroup key={g.key} label={g.label}>
                    {g.items.map((d) => {
                      const n = overrides.filter((o) => o.source_document_id === d.id).length;
                      return (
                        <option key={d.id} value={d.id}>
                          {prettyName(d.filename)}
                          {n ? ` · ${n} saved` : ""}
                        </option>
                      );
                    })}
                  </optgroup>
                ))}
            </select>
          )}
        </StepBox>
        <StepBox n={2} title="Set the scale" done={!!analysis && scaledPages > 0}>
          {!analysis
            ? "Opens once the drawing has loaded."
            : scaledPages === 0
              ? `None of the ${analysis.page_count} pages has a scale yet. On the page you want, pick its scale from the page header, or Calibrate from a dimension you know.`
              : `${scaledPages} of ${analysis.page_count} pages have a scale. Only those pages can be measured.`}
        </StepBox>
        <StepBox n={3} title="Measure and save" done={savedForDiscipline > 0}>
          Click a detected length on the right and tick it, or use <b>+ Two-point measurement</b>.{" "}
          {savedForDiscipline > 0 ? (
            <Link href={`/projects/${projectId}/takeoff`} className="text-blue-700">
              {savedForDiscipline} saved · check them in Takeoff →
            </Link>
          ) : (
            "Nothing saved yet."
          )}
        </StepBox>
      </Card>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {busy && <div className="text-slate-400 text-sm">Loading drawing…</div>}

      {analysisState === "analysing" && (
        <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
          Reading the drawing for scales and lengths. You can look at it meanwhile; this usually takes under a
          minute and the page updates by itself.
        </div>
      )}
      {analysisState === "failed" && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          The drawing could not be read for scales and lengths: {analysisError}{" "}
          <button onClick={() => setAnalysisRetry((n) => n + 1)} className="font-medium underline">
            Try again
          </button>
        </div>
      )}

      {analysis && scaledPages === 0 && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <b>Step 2 first:</b> this drawing has no scale on any page, so nothing can be measured yet. In the grey header
          above the page you want, pick its scale (it is printed in the title block), or press <b>Calibrate</b> and
          click both ends of a dimension whose length you know.
        </div>
      )}

      <div className="grid grid-cols-3 gap-4">
        {/* PDF viewer */}
        <div
          ref={setPane}
          className="col-span-2 overflow-auto rounded-md border border-slate-200 bg-white shadow-sm"
          style={{ maxHeight: "82vh" }}
        >
          {pdfUrl && (
            <div className="sticky top-0 left-0 z-20 flex items-center gap-2 border-b border-slate-200 bg-white/95 px-2 py-1.5 text-sm">
              <span className="text-slate-500">Zoom</span>
              <button
                onClick={() => stepZoom(-1)}
                disabled={zoom <= ZOOMS[0]}
                className="h-7 w-7 rounded border border-slate-300 disabled:opacity-40"
                aria-label="Zoom out"
              >
                −
              </button>
              <select
                value={zoom}
                onChange={(e) => applyZoom(Number(e.target.value))}
                className="h-7 rounded border border-slate-300 px-1 tabular-nums"
                aria-label="Zoom level"
              >
                {ZOOMS.map((z) => (
                  <option key={z} value={z}>
                    {Math.round(z * 100)}%
                  </option>
                ))}
              </select>
              <button
                onClick={() => stepZoom(1)}
                disabled={zoom >= ZOOMS[ZOOMS.length - 1]}
                className="h-7 w-7 rounded border border-slate-300 disabled:opacity-40"
                aria-label="Zoom in"
              >
                +
              </button>
              {zoom !== 1 && (
                <button onClick={() => applyZoom(1)} className="text-xs text-blue-700">
                  Fit width
                </button>
              )}
              <span className="ml-auto text-xs text-slate-400">Pinch or ctrl + scroll on the drawing to zoom</span>
            </div>
          )}
          <div className="p-2">
          {pdfUrl ? (
            <PDFDocument
              file={pdfUrl}
              onLoadSuccess={({ numPages }) => setNumPages(numPages)}
              loading={
                <div className="text-slate-400 text-sm p-4">Rendering PDF…</div>
              }
              error={
                <div className="text-red-600 text-sm p-4">
                  Failed to render PDF.
                </div>
              }
            >
              {Array.from({ length: numPages }, (_, i) => {
                const pageNum = i + 1;
                const meta = analysis?.pages.find((p) => p.page === pageNum);
                const activeRow = activeCandidateKey
                  ? rankedRows.find(
                      ({ candidate }) =>
                        candidateKey(candidate) === activeCandidateKey,
                    )
                  : null;
                const showOverlays =
                  activeRow && meta && activeRow.candidate.page === pageNum;
                const excluded =
                  activeRow && pathExclusions[activeCandidateKey!]
                    ? pathExclusions[activeCandidateKey!]
                    : new Set<string>();
                // One clickable rect per individual stroke. Click toggles
                // its inclusion in the user's refined selection.
                const rects = showOverlays && activeRow && meta
                  ? activeRow.candidate.paths
                      .map((p: PathInCandidate) => {
                        const screen = bboxToScreen(p.bbox, meta, renderWidth);
                        if (!screen) return null;
                        return {
                          id: pathId(p),
                          // Inflate hit-area so hair-thin strokes are
                          // clickable (a horizontal line has zero height).
                          left: screen.left - 2,
                          top: screen.top - 2,
                          width: Math.max(screen.width, 6) + 4,
                          height: Math.max(screen.height, 6) + 4,
                        };
                      })
                      .filter((b): b is NonNullable<typeof b> => b !== null)
                  : [];
                return (
                  <div key={i} className="mb-4 border border-slate-200">
                    <div className="bg-slate-100 px-3 py-1.5 text-xs flex items-center justify-between gap-2">
                      <span className="uppercase tracking-wide text-slate-500 shrink-0">
                        Page {pageNum}
                      </span>
                      {meta && (() => {
                        const eff = scaleByPage.get(pageNum);
                        const hasOverride = scaleOverrides.some(
                          (o) => o.page === pageNum,
                        );
                        const calibrating = calibrate?.page === pageNum;
                        const auto = analysis?.scales.find((s) => s.page === pageNum);
                        const agrees =
                          !!eff?.ppf && !!auto?.measured_ppf && lengthError(eff.ppf, auto.measured_ppf) === null;
                        return (
                          <div className="flex items-center gap-1.5 normal-case">
                            <span
                              className={`text-[10px] ${
                                eff?.ppf ? "text-slate-600" : "text-amber-600 font-medium"
                              }`}
                            >
                              {eff?.ppf
                                ? `${eff.label ?? "scale set"}${
                                    eff.source === "auto"
                                      ? auto?.confidence === "measured"
                                        ? " · from printed dimensions"
                                        : ""
                                      : eff.source === "calibrated"
                                        ? " · calibrated"
                                        : " · manual"
                                  }`
                                : "No scale yet: pick one →"}
                            </span>
                            {agrees && (
                              <span
                                className="text-[10px] text-emerald-700"
                                title={`${auto?.measured_agreeing} of ${auto?.measured_readings} printed dimensions on this sheet measure at this scale`}
                              >
                                ✓ matches {auto?.measured_agreeing} dimensions
                              </span>
                            )}
                            {/* Shows the scale in use, so a page that has one no
                                longer reads "Set scale…" (Amir, 2026-10-26). */}
                            <select
                              value={scaleSelectValue(eff)}
                              onChange={(e) => {
                                const v = e.target.value;
                                if (v && v !== CUSTOM_SCALE) setNamedScale(pageNum, v);
                              }}
                              className={`border bg-white px-1 py-0.5 text-[10px] ${
                                eff?.ppf ? "border-slate-400 font-medium text-slate-800" : "border-slate-300"
                              }`}
                              title={eff?.ppf ? "This page's scale. Pick another to change it." : "Set this page's scale"}
                            >
                              {!eff?.ppf && <option value="">Set scale…</option>}
                              {scaleSelectValue(eff) === CUSTOM_SCALE && (
                                <option value={CUSTOM_SCALE} disabled>
                                  {eff?.label ?? "Calibrated"}
                                </option>
                              )}
                              {NAMED_SCALES.map((s) => (
                                <option key={s.label} value={s.label}>
                                  {s.group}: {s.label}
                                </option>
                              ))}
                            </select>
                            <button
                              onClick={() =>
                                calibrating
                                  ? cancelCalibrate()
                                  : startCalibrate(pageNum)
                              }
                              className={`px-1.5 py-0.5 text-[10px] ${
                                calibrating
                                  ? "bg-emerald-600 text-white"
                                  : "bg-slate-200 text-slate-700 hover:bg-slate-300"
                              }`}
                              title="Click two points on a known dimension, then type its real length"
                            >
                              {calibrating ? "Cancel" : "Calibrate"}
                            </button>
                            {hasOverride && (
                              <button
                                onClick={() => clearScale(pageNum)}
                                className="px-1 py-0.5 text-[10px] text-red-600 hover:text-red-800"
                                title="Remove the manual scale for this page"
                              >
                                Clear
                              </button>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                    {meta?.raster_plan && (
                      <div className="border-b border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                        <b>The plan on this sheet is a picture</b>, not drawn lines, so lengths cannot be found on it
                        automatically and the scale printed in its title block may not match the picture. Press{" "}
                        <b>Calibrate</b> and click both ends of a dimension you know, then use{" "}
                        <b>+ Two-point measurement</b>.
                      </div>
                    )}
                    {(() => {
                      // Step 2 (2026-10-26): say when the scale in use disagrees
                      // with the sheet's own printed dimensions (ID-0: 1/4" on a
                      // plan enlarged to 0.48"/ft, every length 1.9x too long).
                      const auto = analysis?.scales.find((s) => s.page === pageNum);
                      const eff = scaleByPage.get(pageNum);
                      const measured = auto?.measured_ppf;
                      if (!measured || !eff?.ppf) return null;
                      const err = lengthError(eff.ppf, measured);
                      if (err === null) return null;
                      const real = describeScale(measured, eff.label);
                      return (
                        <div className="border-b border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                          <b>This page&apos;s scale does not match its own dimensions.</b> {auto?.measured_agreeing} of{" "}
                          {auto?.measured_readings} printed dimensions measure at <b>{real}</b>, so at{" "}
                          {eff.label ?? "the scale in use"} every length would be {describeLengthError(err)}.{" "}
                          <button
                            onClick={() => applyMeasuredScale(pageNum, measured, auto?.measured_agreeing ?? 0, eff.label)}
                            className="ml-1 bg-amber-600 px-2 py-0.5 text-white hover:bg-amber-700"
                          >
                            Use {real}
                          </button>
                        </div>
                      );
                    })()}
                    {calibrate?.page === pageNum && (
                      <div className="bg-emerald-50 border-b border-emerald-200 px-3 py-2 text-xs text-emerald-800">
                        {!calibrate.first && (
                          <>Click the <strong>start</strong> of a dimension you know the real length of (e.g. a labelled wall).</>
                        )}
                        {calibrate.first && !calibrate.distPdf && (
                          <>Now click the <strong>end</strong> of that dimension.</>
                        )}
                        {calibrate.distPdf && (
                          <div className="flex flex-wrap items-center gap-2">
                            <span>
                              Measured {calibrate.distPdf.toFixed(1)} pdf units. Real length:
                            </span>
                            <input
                              type="text"
                              inputMode="decimal"
                              value={calibrateFeet}
                              onChange={(e) => setCalibrateFeet(e.target.value)}
                              placeholder={`48'-5" or 48.42`}
                              className="w-24 border border-emerald-300 px-1.5 py-0.5 text-xs bg-white"
                            />
                            {(() => {
                              // Before saving, say what scale the click works out
                              // to and how far it is from the one in use, so an
                              // enlarged or reduced plan shows up here.
                              const feet = parseFeetInput(calibrateFeet);
                              if (feet === null) return <span>ft</span>;
                              const ppf = calibrate.distPdf / feet;
                              const prior = scaleByPage.get(pageNum);
                              const err = prior?.ppf ? lengthError(prior.ppf, ppf) : null;
                              return (
                                <span>
                                  = <b>{describeScale(ppf, prior?.label)}</b>
                                  {prior?.ppf && err === null && <> (matches {prior.label ?? "the scale in use"})</>}
                                  {prior?.ppf && err !== null && (
                                    <span className="text-amber-800">
                                      {" "}
                                      ({prior.label ?? "the scale in use"} would make every length{" "}
                                      {describeLengthError(err)})
                                    </span>
                                  )}
                                </span>
                              );
                            })()}
                            <button
                              onClick={saveCalibration}
                              className="px-2 py-0.5 text-xs bg-emerald-600 text-white"
                            >
                              Save scale
                            </button>
                            <button
                              onClick={() =>
                                setCalibrate({
                                  page: pageNum,
                                  first: null,
                                  distPdf: null,
                                  end: null,
                                })
                              }
                              className="px-2 py-0.5 text-xs bg-white border border-slate-300 text-slate-600"
                            >
                              Re-pick
                            </button>
                          </div>
                        )}
                      </div>
                    )}
                    <div className="relative inline-block">
                      <NearViewport
                        root={pane}
                        width={renderWidth}
                        height={meta ? (meta.height / meta.width) * renderWidth : renderWidth * 0.72}
                      >
                        <PDFPage
                          pageNumber={pageNum}
                          width={renderWidth}
                          devicePixelRatio={devicePixelRatioFor(
                            renderWidth,
                            meta ? (meta.height / meta.width) * renderWidth : renderWidth * 0.72,
                          )}
                          renderTextLayer={false}
                          renderAnnotationLayer={false}
                        />
                      </NearViewport>
                      {/* AI-detected stroke overlays (per-stroke toggle) */}
                      {!measureMode && calibrate?.page !== pageNum && rects.map((b) => {
                        const isExcluded = excluded.has(b.id);
                        return (
                          <div
                            key={b.id}
                            onClick={(e) => {
                              e.stopPropagation();
                              togglePathExclusion(activeCandidateKey!, b.id);
                            }}
                            className="absolute cursor-pointer"
                            style={{
                              left: b.left,
                              top: b.top,
                              width: b.width,
                              height: b.height,
                              backgroundColor: isExcluded
                                ? "rgba(100, 116, 139, 0.25)"
                                : "rgba(220, 38, 38, 0.45)",
                              outline: isExcluded
                                ? "1px dashed #64748b"
                                : "1px solid #dc2626",
                            }}
                            title={
                              isExcluded
                                ? "Click to include this stroke"
                                : "Click to remove this stroke from the group"
                            }
                          />
                        );
                      })}
                      {/* Saved manual measurements (blue lines) */}
                      {meta && (() => {
                        const scale = renderWidth / meta.width;
                        const manuals = overrides.filter(
                          (o) =>
                            o.source === "manual_measure" &&
                            o.source_document_id === selectedDoc &&
                            o.source_page === pageNum,
                        );
                        if (manuals.length === 0) return null;
                        return (
                          <svg
                            className="absolute inset-0 pointer-events-none"
                            width={renderWidth}
                            height={meta.height * scale}
                          >
                            {manuals.map((m) => {
                              const line = parseManualLine(m.notes);
                              if (!line) return null;
                              const x1 = line.start[0] * scale;
                              const y1 = line.start[1] * scale;
                              const x2 = line.end[0] * scale;
                              const y2 = line.end[1] * scale;
                              const midX = (x1 + x2) / 2;
                              const midY = (y1 + y2) / 2;
                              return (
                                <g key={m.id}>
                                  <line
                                    x1={x1} y1={y1} x2={x2} y2={y2}
                                    stroke="#2563eb" strokeWidth={2.5}
                                  />
                                  <circle cx={x1} cy={y1} r={3} fill="#2563eb" />
                                  <circle cx={x2} cy={y2} r={3} fill="#2563eb" />
                                  <text
                                    x={midX} y={midY - 6}
                                    fill="#1e40af" fontSize={11}
                                    fontWeight={600}
                                    textAnchor="middle"
                                    style={{ paintOrder: "stroke", stroke: "white", strokeWidth: 3 }}
                                  >
                                    {m.value !== null ? m.value.toFixed(1) : "?"} ft
                                  </text>
                                </g>
                              );
                            })}
                          </svg>
                        );
                      })()}
                      {/* In-progress first-point marker for measure mode */}
                      {meta && firstPoint && firstPoint.page === pageNum && (() => {
                        const scale = renderWidth / meta.width;
                        return (
                          <div
                            className="absolute pointer-events-none"
                            style={{
                              left: firstPoint.postX * scale - 4,
                              top: firstPoint.postY * scale - 4,
                              width: 8,
                              height: 8,
                              backgroundColor: "#2563eb",
                              borderRadius: "50%",
                              border: "2px solid white",
                            }}
                          />
                        );
                      })()}
                      {/* Click overlay for measure mode */}
                      {measureMode && (
                        <div
                          className="absolute inset-0 cursor-crosshair"
                          onClick={(e) => handleMeasureClick(pageNum, e)}
                          style={{ backgroundColor: "rgba(37, 99, 235, 0.02)" }}
                        />
                      )}
                      {/* Calibration markers (green) for this page */}
                      {meta && calibrate?.page === pageNum && (() => {
                        const scale = renderWidth / meta.width;
                        const pts: { x: number; y: number }[] = [];
                        if (calibrate.first)
                          pts.push({
                            x: calibrate.first.postX * scale,
                            y: calibrate.first.postY * scale,
                          });
                        if (calibrate.end)
                          pts.push({
                            x: calibrate.end.postX * scale,
                            y: calibrate.end.postY * scale,
                          });
                        return (
                          <svg
                            className="absolute inset-0 pointer-events-none"
                            width={renderWidth}
                            height={meta.height * scale}
                          >
                            {pts.length === 2 && (
                              <line
                                x1={pts[0].x} y1={pts[0].y}
                                x2={pts[1].x} y2={pts[1].y}
                                stroke="#059669" strokeWidth={2.5}
                              />
                            )}
                            {pts.map((p, idx) => (
                              <circle
                                key={idx}
                                cx={p.x} cy={p.y} r={4}
                                fill="#059669" stroke="white" strokeWidth={2}
                              />
                            ))}
                          </svg>
                        );
                      })()}
                      {/* Click overlay for calibrate mode (this page only) */}
                      {calibrate?.page === pageNum && (
                        <div
                          className="absolute inset-0 cursor-crosshair"
                          onClick={(e) => handleCalibrateClick(pageNum, e)}
                          style={{ backgroundColor: "rgba(16, 185, 129, 0.04)" }}
                        />
                      )}
                    </div>
                  </div>
                );
              })}
            </PDFDocument>
          ) : (
            <div className="text-slate-400 text-sm p-4">Select a drawing.</div>
          )}
          </div>
        </div>

        {/* Candidate panel */}
        <Card className="p-0 overflow-hidden">
          <div className="border-b border-slate-200 px-4 py-2 flex items-center justify-between">
            <h2 className="font-semibold text-slate-800 text-sm">
              Detected lengths
            </h2>
            <button
              onClick={measureMode ? cancelMeasure : enterMeasureMode}
              className={`px-2 py-1 text-xs uppercase tracking-wide ${
                measureMode
                  ? "bg-blue-600 text-white"
                  : "bg-slate-100 text-slate-700 hover:bg-slate-200"
              }`}
              title={
                measureMode
                  ? "Exit measure mode"
                  : "Click two points on the PDF to drop a manual measurement"
              }
            >
              {measureMode ? "Cancel measuring" : "+ Two-point measurement"}
            </button>
          </div>
          {measureMode && (
            <div className="bg-blue-50 border-b border-blue-200 px-3 py-2 text-xs text-blue-700">
              {!firstPoint && !pendingMeasure && (
                <>Click <strong>two points</strong> on any scaled page to measure.</>
              )}
              {firstPoint && !pendingMeasure && (
                <>First point set on page {firstPoint.page}. Click second point to compute the distance.</>
              )}
              {pendingMeasure && (
                <>
                  <div className="font-semibold text-blue-800 mb-1">
                    ≈ {pendingMeasure.realFeet.toFixed(1)} ft on page {pendingMeasure.page}
                  </div>
                  <label className="block mb-1">
                    Quantity type
                    <select
                      value={measureType}
                      onChange={(e) => setMeasureType(e.target.value)}
                      className="block w-full mt-0.5 border border-blue-300 px-1.5 py-0.5 text-xs bg-white"
                    >
                      <option value="wall_or_partition">Wall / partition</option>
                      <option value="piping">Piping</option>
                      <option value="conduit">Conduit</option>
                      <option value="ductwork">Ductwork</option>
                      <option value="trim">Trim / baseboard</option>
                      <option value="dimension_line">Dimension reference</option>
                      <option value="other">Other (see notes)</option>
                    </select>
                  </label>
                  <label className="block mb-2">
                    Description (optional)
                    <input
                      type="text"
                      value={measureDescription}
                      onChange={(e) => setMeasureDescription(e.target.value)}
                      placeholder="e.g. North-wall partition, kitchen to corridor"
                      className="block w-full mt-0.5 border border-blue-300 px-1.5 py-0.5 text-xs bg-white"
                    />
                  </label>
                  <div className="flex gap-2">
                    <button
                      onClick={saveMeasure}
                      className="px-2 py-1 text-xs bg-blue-600 text-white"
                    >
                      Save measurement
                    </button>
                    <button
                      onClick={() => setPendingMeasure(null)}
                      className="px-2 py-1 text-xs bg-white border border-slate-300 text-slate-700"
                    >
                      Re-do
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
          {analysis ? (
            <div className="overflow-auto" style={{ maxHeight: "78vh" }}>
              <div className="border-b border-slate-200 px-3 py-2 text-xs text-slate-500 bg-slate-50">
                <div>
                  {scaledPages} of {analysis.page_count} pages have a scale · {rankedRows.length} length
                  {rankedRows.length === 1 ? "" : "s"} found on them. Wall runs are grouped from the line-work by
                  line weight; click one to see its lines on the PDF, remove any that are not part of it, then tick it.
                </div>
              </div>
              {rankedRows.length === 0 ? (
                <p className="px-3 py-3 text-sm text-slate-500">
                  {scaledPages === 0
                    ? "Nothing yet: set a scale on a page first (step 2). Lengths found on that page appear here."
                    : "No lengths found on the scaled pages. Use + Two-point measurement to measure by hand."}
                </p>
              ) : (
                // Page-grouped rendering — one section per page with a
                // sticky header showing scale + count, then the rows.
                analysis.pages.map((pg) => {
                  const rowsForPage = rowsByPage.get(pg.page) ?? [];
                  const eff = scaleByPage.get(pg.page);
                  const scaleInfo = eff?.ppf
                    ? { scale_label: eff.label ?? "(scale set)" }
                    : null;
                  if (rowsForPage.length === 0) {
                    // Render an empty-page note so the user knows the page
                    // was processed but has no candidates (cover sheet,
                    // unscaled page, etc.).
                    const reason = !eff?.ppf
                      ? "no scale set — use the page's Set scale / Calibrate control"
                      : "no lengths found";
                    return (
                      <div
                        key={pg.page}
                        className="border-b border-slate-100 px-3 py-1.5 text-xs text-slate-400 bg-slate-50/50"
                      >
                        Page {pg.page} — {reason}
                      </div>
                    );
                  }
                  return (
                    <div key={pg.page}>
                      <div className="sticky top-0 bg-slate-100 border-b border-slate-200 px-3 py-1.5 text-[11px] uppercase tracking-wide text-slate-600 z-10">
                        <span className="font-semibold">Page {pg.page}</span>
                        <span className="ml-2 text-slate-500 normal-case">
                          {rowsForPage.length} candidate{rowsForPage.length === 1 ? "" : "s"}
                        </span>
                        {scaleInfo?.scale_label && (
                          <span className="ml-2 text-slate-400 normal-case">
                            · scale {scaleInfo.scale_label}
                          </span>
                        )}
                      </div>
                      {rowsForPage.map(({ row, candidate }) => {
                  const cand = candidate!;
                  const key = candidateKey(cand);
                  const isConfirmed = confirmedByKey.has(key);
                  const isActive = activeCandidateKey === key;
                  const totalPaths = row.path_count;
                  // Live recompute from current exclusion state, using the
                  // effective (possibly overridden) page scale.
                  const scale = ppfFor(row.page);
                  const stats = refinedStats(cand, scale, pathExclusions[key]);
                  const isRefined = stats.includedCount < stats.totalCount;
                  return (
                    <div
                      key={key}
                      onClick={() => setActiveCandidateKey(isActive ? null : key)}
                      className={`border-b border-slate-100 px-3 py-2 text-sm cursor-pointer ${
                        isActive ? "bg-amber-50" : "hover:bg-slate-50"
                      }`}
                      title={hintFor(row.classification, drawingDiscipline)}
                    >
                      <div className="flex items-start gap-2">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleCandidate(cand, scale);
                          }}
                          className={`w-7 h-7 flex items-center justify-center text-sm shrink-0 mt-0.5 ${
                            isConfirmed
                              ? "bg-green-600 text-white"
                              : "bg-slate-100 text-slate-500 hover:bg-slate-200"
                          }`}
                          title={
                            isConfirmed
                              ? "Click to un-confirm"
                              : isRefined
                                ? `Confirm refined selection (${stats.includedCount} of ${stats.totalCount} strokes)`
                                : "Click to confirm"
                          }
                        >
                          {isConfirmed ? "✓" : ""}
                        </button>
                        <div className="flex-1 min-w-0">
                          <div className="text-[11px] uppercase tracking-wide text-slate-400">
                            Page {row.page} ·{" "}
                            {stats.realFeet !== null
                              ? `${stats.realFeet.toFixed(0)} ft`
                              : `${row.real_feet?.toFixed(0)} ft`}
                            {isRefined && " (refined)"}
                          </div>
                          <div className="text-sm font-medium text-slate-800">
                            {labelFor(row.classification, drawingDiscipline)}
                          </div>
                          <div className="text-xs text-slate-500 mt-0.5">
                            {isRefined
                              ? `${stats.includedCount} of ${stats.totalCount} strokes selected`
                              : `${totalPaths} stroke${totalPaths === 1 ? "" : "s"} grouped`}
                            {row.classification === "wall_filled"
                              ? ppfFor(row.page)
                                ? ` · drawn ~${Math.round((row.stroke_width / ppfFor(row.page)!) * 12)}" thick`
                                : ""
                              : ` · ~${row.stroke_width.toFixed(2)}pt line weight`}
                          </div>
                          {isActive && (
                            <div className="text-xs text-amber-700 mt-1">
                              ← Click any red stroke on the PDF to remove it
                              from the group. Click again to add it back.
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
                    </div>
                  );
                })
              )}
            </div>
          ) : (
            <p className="px-3 py-3 text-sm text-slate-400">
              {analysisState === "analysing" || analysisState === "loading"
                ? "Reading the drawing…"
                : analysisState === "failed"
                  ? "Could not read this drawing."
                  : "No analysis yet."}
            </p>
          )}
        </Card>
      </div>

      {/* What has been saved for this discipline. It listed every CAD reading
          twice (one per Plan file) under raw codes like wall_lf_a-wall. */}
      {savedRows.length > 0 && (
        <Card className="p-4">
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="font-semibold text-slate-800 text-sm">Saved for {discipline}</h2>
            <Link href={`/projects/${projectId}/takeoff`} className="text-xs text-blue-700">
              Check them in Takeoff →
            </Link>
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-slate-500">
              <tr>
                <th className="py-1 font-medium">Quantity</th>
                <th className="py-1 pr-4 font-medium text-right">Value</th>
                <th className="py-1 font-medium">Drawing</th>
                <th className="py-1 font-medium">How</th>
                <th className="py-1 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {savedRows.map(({ o, copies }) => {
                const desc = o.source === "manual_measure" ? parseManualLine(o.notes)?.description : undefined;
                const doc = allDocs.find((d) => d.id === o.source_document_id);
                return (
                  <tr key={o.id} className="border-t border-slate-100">
                    <td className="py-1">
                      {o.source === "cad_extract"
                        ? measurementLabel(o.quantity_type)
                        : labelFor(o.quantity_type, discipline.toLowerCase())}
                      {desc && <span className="ml-2 text-xs text-slate-500">— {desc}</span>}
                    </td>
                    <td className="py-1 pr-4 text-right tabular-nums">
                      {o.value !== null ? `${o.value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${o.unit}` : "—"}
                    </td>
                    <td className="py-1 text-xs text-slate-500">
                      {doc ? prettyName(doc.filename) : o.source_document_id ? "Drawing" : "—"}
                      {o.source_page ? `, p. ${o.source_page}` : ""}
                      {copies > 1 ? ` · same reading in ${copies} files` : ""}
                    </td>
                    <td className="py-1 text-xs text-slate-500">{SOURCE_LABEL[o.source] ?? o.source}</td>
                    <td className="py-1 text-right">
                      {o.source === "manual_measure" && (
                        <button
                          onClick={() => deleteMeasure(o.id)}
                          className="text-xs text-red-600 hover:text-red-800"
                          title="Delete this measurement"
                        >
                          Delete
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

function StepBox({ n, title, done, children }: { n: number; title: string; done: boolean; children: React.ReactNode }) {
  return (
    <div className={`rounded-md border p-3 text-[13px] text-slate-700 ${done ? "border-emerald-600" : "border-slate-300"}`}>
      <div className="mb-1 text-sm font-semibold text-slate-900">
        {n} · {title} {done ? <span className="text-emerald-700">✓</span> : null}
      </div>
      {children}
    </div>
  );
}
