"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ActivityActual } from "@/lib/workbench";

type Activity = {
  id?: string;
  activity_code: string;
  name: string;
  phase: string;
  start_date: string | null;
  finish_date: string | null;
  // FastAPI serialises Postgres `numeric` columns as strings (not JS
  // numbers). Accept either and coerce in render.
  expected_wd: number | string | null;
  total_float_wd: number | null;
  is_critical: boolean;
  is_milestone: boolean;
};

type Inspection = {
  id?: string;
  activity_id?: string | null;
  authority?: string;
  inspection_type?: string;
};

// ── Zoom levels ──────────────────────────────────────────────────────────
// "days" gives ~1-2 weeks per viewport, "weeks" ~2-3 months, "months" the
// whole project. PX_PER_DAY drives bar widths and tick density.
type Zoom = "days" | "weeks" | "months";
const ZOOM_PX_PER_DAY: Record<Zoom, number> = {
  days: 28,
  weeks: 10,
  months: 5,
};
const ZOOM_LABEL: Record<Zoom, string> = {
  days: "Days",
  weeks: "Weeks",
  months: "Months",
};

// Add breathing room around the project window so today / inspection dots
// at the edges don't clip against the gutter or axis end.
const PRE_BUFFER_DAYS = 7;
const POST_BUFFER_DAYS = 14;

// Drag-to-resize bounds for the left label gutter.
const MIN_LABEL_W = 140;
const MAX_LABEL_W = 600;
const DEFAULT_LABEL_W = 240;

const DAY_MS = 1000 * 60 * 60 * 24;

/**
 * Interactive SVG Gantt — phase-grouped rows, horizontal bars on a date
 * axis. Critical activities render in amber, non-critical in blue,
 * milestones in dark slate as diamonds, inspections as small red dots on
 * the host activity's finish.
 *
 * Interactive controls:
 *   - Zoom (Days / Weeks / Months) re-scales the timeline width.
 *   - "Today" button scrolls the timeline so the today marker is centred.
 *   - The left label gutter is drag-resizable (cursor on the divider).
 */
export function GanttChart({
  activities,
  inspections,
  projectStart,
  projectEnd,
  criticalCodes,
  actuals = {},
}: {
  activities: Activity[];
  inspections: Inspection[];
  projectStart: string | null;
  projectEnd: string | null;
  criticalCodes: string[];
  actuals?: Record<string, ActivityActual>;
}) {
  const [hoverCode, setHoverCode] = useState<string | null>(null);
  const [zoom, setZoom] = useState<Zoom>("months");
  const [labelW, setLabelW] = useState(DEFAULT_LABEL_W);
  // When ON: critical-path activities stay full opacity / 1.5px outline,
  // every non-critical bar + label drops to ~15% opacity, and the
  // background grid behind critical bars gets a faint amber wash. Lets
  // you visually trace the critical chain at any zoom level.
  const [cpFocus, setCpFocus] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);

  const cpSet = useMemo(() => new Set(criticalCodes), [criticalCodes]);

  // ── Drag-to-resize the label gutter ────────────────────────────────────
  // Global pointermove/pointerup so the drag survives even when the cursor
  // leaves the handle's tiny hit area.
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      if (!dragRef.current) return;
      const next =
        dragRef.current.startW + (e.clientX - dragRef.current.startX);
      setLabelW(Math.max(MIN_LABEL_W, Math.min(MAX_LABEL_W, next)));
    };
    const onUp = () => {
      if (dragRef.current) {
        dragRef.current = null;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, []);

  const startDrag = (e: React.PointerEvent) => {
    dragRef.current = { startX: e.clientX, startW: labelW };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  // ── Layout — recomputes on zoom / labelW / data change ────────────────
  const view = useMemo(() => {
    if (!projectStart || activities.length === 0) return null;

    const PX_PER_DAY = ZOOM_PX_PER_DAY[zoom];
    const pStart = new Date(projectStart).getTime();
    const explicitEnd = projectEnd ? new Date(projectEnd).getTime() : 0;
    const maxActFinish = Math.max(
      0,
      ...activities
        .map((a) => (a.finish_date ? new Date(a.finish_date).getTime() : 0))
        .filter((n) => n > 0),
    );
    const pEnd = Math.max(explicitEnd, maxActFinish);
    if (pEnd <= pStart) return null;

    // Buffered axis bounds so the project window doesn't sit flush with
    // the gutter / right edge, and so "today" can render before start.
    const startMs = pStart - PRE_BUFFER_DAYS * DAY_MS;
    const endMs = pEnd + POST_BUFFER_DAYS * DAY_MS;
    const totalMs = endMs - startMs;
    if (totalMs <= 0) return null;

    const ROW_H = 22;
    const PHASE_H = 28;
    // Two-row axis header on weeks + days zoom (month band over day/week
    // numbers); single-row month header on months zoom.
    const TWO_ROW_AXIS = PX_PER_DAY >= 8;
    const TOP_PAD = TWO_ROW_AXIS ? 56 : 36;
    const days = totalMs / DAY_MS;
    const TIMELINE_W = Math.max(640, Math.ceil(days * PX_PER_DAY));
    const TOTAL_W = labelW + TIMELINE_W + 20;

    // Phase grouping (preserve first-seen order)
    const phases: string[] = [];
    const byPhase = new Map<string, Activity[]>();
    for (const a of activities) {
      if (!byPhase.has(a.phase)) {
        byPhase.set(a.phase, []);
        phases.push(a.phase);
      }
      byPhase.get(a.phase)!.push(a);
    }

    // Compute y for each row + total height
    let y = TOP_PAD;
    const rows: {
      kind: "phase" | "act";
      label: string;
      y: number;
      act?: Activity;
    }[] = [];
    for (const ph of phases) {
      rows.push({ kind: "phase", label: ph, y });
      y += PHASE_H;
      for (const a of byPhase.get(ph)!) {
        rows.push({ kind: "act", label: a.name, y, act: a });
        y += ROW_H;
      }
    }
    const TOTAL_H = y + 20;

    // Date → x (within timeline, NOT including labelW offset)
    const xOf = (d: string | null | undefined) => {
      if (!d) return 0;
      const ms = new Date(d).getTime();
      return ((ms - startMs) / totalMs) * TIMELINE_W;
    };

    // ── Axis ───────────────────────────────────────────────────────────
    // Two-tier on days/weeks zoom: month band on top, day/week numbers
    // below. Single-tier on months zoom. The day-of-month-only labels
    // (no month) avoid the "Jul 6 Jul 7 Jul 8" overlap the previous
    // version hit at 28 px/day.
    type Tick = { x: number; label: string; major?: boolean };
    type Band = { x: number; w: number; label: string };
    const ticks: Tick[] = [];
    const bands: Band[] = [];

    const addMonthBands = () => {
      // One band per calendar month between startMs and endMs.
      const m = new Date(startMs);
      m.setDate(1);
      m.setHours(0, 0, 0, 0);
      while (m.getTime() <= endMs) {
        const next = new Date(m);
        next.setMonth(next.getMonth() + 1);
        const x = xOf(m.toISOString().slice(0, 10));
        const xEnd = xOf(next.toISOString().slice(0, 10));
        bands.push({
          x,
          w: xEnd - x,
          label: m.toLocaleDateString("en-CA", {
            month: "long",
            year: "numeric",
          }),
        });
        m.setTime(next.getTime());
      }
    };

    if (PX_PER_DAY >= 22) {
      // Day-of-month numbers, bold every Monday. Month label on the band.
      addMonthBands();
      const d = new Date(startMs);
      d.setHours(0, 0, 0, 0);
      while (d.getTime() <= endMs) {
        ticks.push({
          x: xOf(d.toISOString().slice(0, 10)),
          label: String(d.getDate()),
          major: d.getDay() === 1,
        });
        d.setDate(d.getDate() + 1);
      }
    } else if (PX_PER_DAY >= 8) {
      // Day-of-month for each Monday. Month label on the band.
      addMonthBands();
      const d = new Date(startMs);
      d.setHours(0, 0, 0, 0);
      while (d.getDay() !== 1) d.setDate(d.getDate() + 1);
      while (d.getTime() <= endMs) {
        ticks.push({
          x: xOf(d.toISOString().slice(0, 10)),
          label: String(d.getDate()),
          major: d.getDate() <= 7,
        });
        d.setDate(d.getDate() + 7);
      }
    } else {
      // Single-tier monthly ticks (whole-project view).
      const m = new Date(startMs);
      m.setDate(1);
      while (m.getTime() < endMs + 31 * DAY_MS) {
        ticks.push({
          x: xOf(m.toISOString().slice(0, 10)),
          label: m.toLocaleDateString("en-CA", {
            month: "short",
            year: "2-digit",
          }),
          major: m.getMonth() === 0,
        });
        m.setMonth(m.getMonth() + 1);
      }
    }

    // Inspections indexed by activity_id for marker render
    const inspByAct = new Map<string, Inspection[]>();
    for (const i of inspections) {
      if (!i.activity_id) continue;
      const list = inspByAct.get(i.activity_id) ?? [];
      list.push(i);
      inspByAct.set(i.activity_id, list);
    }

    // Today + project-start guide lines (x within timeline)
    const todayMs = Date.now();
    const todayX =
      todayMs >= startMs && todayMs <= endMs
        ? ((todayMs - startMs) / totalMs) * TIMELINE_W
        : null;
    const pStartX = ((pStart - startMs) / totalMs) * TIMELINE_W;

    return {
      ROW_H,
      PHASE_H,
      TOP_PAD,
      TWO_ROW_AXIS,
      TIMELINE_W,
      TOTAL_W,
      TOTAL_H,
      rows,
      ticks,
      bands,
      xOf,
      inspByAct,
      todayX,
      pStartX,
    };
  }, [activities, inspections, projectStart, projectEnd, zoom, labelW]);

  // Jump-to-today: scroll the container so the today line sits ~25% in.
  const jumpToToday = () => {
    if (!view?.todayX || !containerRef.current) return;
    const targetX = labelW + view.todayX;
    const containerW = containerRef.current.clientWidth;
    containerRef.current.scrollTo({
      left: Math.max(0, targetX - containerW * 0.25),
      behavior: "smooth",
    });
  };

  if (!view) {
    return (
      <div className="text-sm text-slate-500 italic py-8 text-center">
        No timeline data available
      </div>
    );
  }

  return (
    <div className="border border-slate-200 bg-white">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 px-3 py-2 text-xs">
        <div className="flex items-center gap-1">
          <span className="text-slate-500 mr-1">Zoom:</span>
          {(["days", "weeks", "months"] as Zoom[]).map((z) => (
            <button
              key={z}
              type="button"
              onClick={() => setZoom(z)}
              className={`px-2 py-1 rounded border text-xs font-medium ${
                zoom === z
                  ? "bg-blue-600 text-white border-blue-600"
                  : "bg-white text-slate-700 border-slate-300 hover:bg-slate-50"
              }`}
            >
              {ZOOM_LABEL[z]}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={jumpToToday}
          disabled={view.todayX == null}
          className="px-2 py-1 rounded border border-slate-300 bg-white text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
          title={
            view.todayX == null
              ? "Today is outside the project window"
              : "Scroll to today"
          }
        >
          Today
        </button>
        <button
          type="button"
          onClick={() => setCpFocus((v) => !v)}
          className={`px-2 py-1 rounded border text-xs font-medium ${
            cpFocus
              ? "bg-amber-500 text-white border-amber-500"
              : "bg-white text-slate-700 border-slate-300 hover:bg-slate-50"
          }`}
          title="Highlight only critical-path activities"
        >
          Critical path {cpFocus ? "on" : "off"}
        </button>
        <button
          type="button"
          onClick={() => setHelpOpen((v) => !v)}
          className="ml-auto px-2 py-1 rounded border border-slate-300 bg-white text-xs font-medium text-slate-700 hover:bg-slate-50"
          aria-expanded={helpOpen}
        >
          {helpOpen ? "Hide help" : "How to read this"}
        </button>
      </div>

      {helpOpen && (
        <div className="border-b border-slate-200 bg-blue-50/40 px-4 py-3 text-xs text-slate-700 space-y-3">
          <div>
            <div className="font-semibold text-slate-800 mb-1">
              What you&rsquo;re looking at
            </div>
            <p>
              A horizontal timeline. Each activity is a bar from its planned
              start to finish. Rows are grouped by phase. The bar&rsquo;s
              colour tells you whether the activity is on the critical path.
            </p>
          </div>
          <div>
            <div className="font-semibold text-slate-800 mb-1">
              Bar colours &amp; markers
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
              <span>
                <span
                  className="inline-block w-3 h-3 rounded-sm mr-1.5 align-middle"
                  style={{ background: "#3b82f6" }}
                />
                Blue bar — activity with slack
              </span>
              <span>
                <span
                  className="inline-block w-3 h-3 rounded-sm mr-1.5 align-middle"
                  style={{ background: "#f59e0b" }}
                />
                Amber bar — on critical path (any delay slips the project)
              </span>
              <span>
                <span
                  className="inline-block mr-1.5 align-middle"
                  style={{
                    width: 0,
                    height: 0,
                    borderLeft: "5px solid transparent",
                    borderRight: "5px solid transparent",
                    borderBottom: "8px solid #1e293b",
                  }}
                />
                Diamond — milestone
              </span>
              <span>
                <span
                  className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle"
                  style={{ background: "#dc2626" }}
                />
                Red dot — AHJ inspection at end of activity
              </span>
            </div>
          </div>
          <div>
            <div className="font-semibold text-slate-800 mb-1">
              Date axis &amp; guides
            </div>
            <ul className="list-disc list-inside space-y-0.5">
              <li>
                On <strong>Weeks</strong> and <strong>Days</strong> zoom the
                top row is a month band (&ldquo;July 2026&rdquo;); the bottom
                row is the day or week number with Mondays bolded.
              </li>
              <li>
                Dashed grey line = <strong>Project start</strong>. Cyan line
                + &ldquo;TODAY&rdquo; pill = today&rsquo;s date.
              </li>
            </ul>
          </div>
          <div>
            <div className="font-semibold text-slate-800 mb-1">Controls</div>
            <ul className="list-disc list-inside space-y-0.5">
              <li>
                <strong>Zoom</strong> changes the time density:{" "}
                <em>Months</em> = whole project, <em>Weeks</em> = 2-3 months
                per viewport, <em>Days</em> = 1-2 weeks per viewport.
              </li>
              <li>
                <strong>Today</strong> scrolls the timeline so today sits
                25% from the left edge.
              </li>
              <li>
                <strong>Critical path</strong> dims non-critical bars and
                outlines the critical chain in amber so you can trace it.
              </li>
              <li>
                Drag the divider between the task column and the timeline to
                widen / narrow the task names.
              </li>
              <li>Hover any bar for full duration and float details.</li>
            </ul>
          </div>
        </div>
      )}

      {/* Chart */}
      <div
        ref={containerRef}
        className="overflow-x-auto relative"
        style={{ scrollbarWidth: "thin" }}
      >
        <svg
          width={view.TOTAL_W}
          height={view.TOTAL_H}
          className="block"
          style={{ fontFamily: "ui-sans-serif, system-ui, sans-serif" }}
        >
          {/* Gutter divider line (visual; resize handle is the div below) */}
          <line
            x1={labelW}
            y1={0}
            x2={labelW}
            y2={view.TOTAL_H}
            stroke="#cbd5e1"
            strokeWidth={1}
          />

          {/* Top axis row — month bands (two-tier zoom only) */}
          {view.TWO_ROW_AXIS &&
            view.bands.map((b, i) => (
              <g key={`band-${i}`}>
                <rect
                  x={labelW + b.x}
                  y={0}
                  width={b.w}
                  height={26}
                  fill={i % 2 === 0 ? "#f8fafc" : "#f1f5f9"}
                />
                <line
                  x1={labelW + b.x}
                  y1={0}
                  x2={labelW + b.x}
                  y2={26}
                  stroke="#e2e8f0"
                />
                <text
                  x={labelW + b.x + b.w / 2}
                  y={17}
                  fontSize="10"
                  fontWeight={600}
                  fill="#1e293b"
                  textAnchor="middle"
                >
                  {b.w > 80 ? b.label : b.label.split(" ")[0]}
                </text>
              </g>
            ))}
          {view.TWO_ROW_AXIS && (
            <line
              x1={labelW}
              y1={26}
              x2={view.TOTAL_W}
              y2={26}
              stroke="#cbd5e1"
            />
          )}

          {/* Bottom axis row — day/week ticks (or single-row month ticks) */}
          {view.ticks.map((t, i) => (
            <g key={i}>
              <line
                x1={labelW + t.x}
                y1={view.TWO_ROW_AXIS ? 26 : view.TOP_PAD - 4}
                x2={labelW + t.x}
                y2={view.TOTAL_H}
                stroke={t.major ? "#e2e8f0" : "#f1f5f9"}
              />
              <text
                x={labelW + t.x + (view.TWO_ROW_AXIS ? 0 : 4)}
                y={view.TOP_PAD - 10}
                fontSize={view.TWO_ROW_AXIS ? "9.5" : "10"}
                fill={t.major ? "#1e293b" : "#64748b"}
                fontWeight={t.major ? 600 : 400}
                textAnchor={view.TWO_ROW_AXIS ? "middle" : "start"}
              >
                {t.label}
              </text>
            </g>
          ))}

          {/* Project-start guide */}
          <g>
            <line
              x1={labelW + view.pStartX}
              y1={view.TOP_PAD - 4}
              x2={labelW + view.pStartX}
              y2={view.TOTAL_H}
              stroke="#94a3b8"
              strokeDasharray="3 3"
              strokeWidth={1}
            />
            <text
              x={labelW + view.pStartX + 4}
              y={view.TOP_PAD - 22}
              fontSize="9"
              fill="#475569"
              fontWeight={600}
            >
              Start
            </text>
          </g>

          {/* Today line */}
          {view.todayX != null && (
            <g>
              <line
                x1={labelW + view.todayX}
                y1={view.TOP_PAD - 4}
                x2={labelW + view.todayX}
                y2={view.TOTAL_H}
                stroke="#0ea5e9"
                strokeWidth={1.5}
              />
              <rect
                x={labelW + view.todayX - 22}
                y={view.TOP_PAD - 26}
                width={44}
                height={14}
                fill="#0ea5e9"
                rx={2}
              />
              <text
                x={labelW + view.todayX}
                y={view.TOP_PAD - 16}
                fontSize="9"
                fill="#fff"
                fontWeight={700}
                textAnchor="middle"
              >
                TODAY
              </text>
            </g>
          )}

          {/* Rows */}
          {view.rows.map((r, i) => {
            if (r.kind === "phase") {
              return (
                <g key={i}>
                  <rect
                    x={0}
                    y={r.y}
                    width={view.TOTAL_W}
                    height={view.PHASE_H}
                    fill="#f8fafc"
                  />
                  <text
                    x={8}
                    y={r.y + 18}
                    fontSize="11"
                    fontWeight={600}
                    fill="#0f172a"
                  >
                    {r.label}
                  </text>
                </g>
              );
            }
            // Activity row
            const a = r.act!;
            const x1 = view.xOf(a.start_date);
            const x2 = view.xOf(a.finish_date);
            const w = Math.max(3, x2 - x1);
            const crit = a.is_critical || cpSet.has(a.activity_code);
            const milestone = a.is_milestone;
            const float = a.total_float_wd ?? 0;
            const isHover = hoverCode === a.activity_code;
            const barColor = milestone
              ? "#1e293b"
              : crit
                ? "#f59e0b"
                : "#3b82f6";
            // Critical-path focus: dim non-critical bars + labels, leave
            // critical at full opacity with a 1.5px amber outline so the
            // chain pops at any zoom.
            const dim = cpFocus && !crit;
            const barOpacity = dim ? 0.18 : 0.88;
            const labelOpacity = dim ? 0.3 : 1;
            const critOutline = cpFocus && crit;

            // Truncate the activity name to fit the label column.
            const maxNameChars = Math.max(8, Math.floor((labelW - 56) / 6));
            const displayName =
              a.name.length > maxNameChars
                ? a.name.slice(0, maxNameChars - 1) + "…"
                : a.name;

            return (
              <g
                key={i}
                onMouseEnter={() => setHoverCode(a.activity_code)}
                onMouseLeave={() => setHoverCode(null)}
              >
                {isHover && (
                  <rect
                    x={0}
                    y={r.y}
                    width={view.TOTAL_W}
                    height={view.ROW_H}
                    fill="#fef3c7"
                    opacity={0.3}
                  />
                )}
                {/* Faint amber wash behind critical rows when focus is on */}
                {critOutline && (
                  <rect
                    x={labelW}
                    y={r.y}
                    width={view.TIMELINE_W}
                    height={view.ROW_H}
                    fill="#f59e0b"
                    opacity={0.06}
                  />
                )}
                <text
                  x={8}
                  y={r.y + 15}
                  fontSize="10"
                  fill="#64748b"
                  fontWeight={crit ? 600 : 400}
                  opacity={labelOpacity}
                >
                  {a.activity_code}
                </text>
                <text
                  x={50}
                  y={r.y + 15}
                  fontSize="10"
                  fill="#1e293b"
                  opacity={labelOpacity}
                  fontWeight={critOutline ? 600 : 400}
                >
                  {displayName}
                  <title>{a.name}</title>
                </text>
                {milestone ? (
                  <polygon
                    points={`${labelW + x1 - 6},${r.y + 11} ${labelW + x1},${r.y + 5} ${labelW + x1 + 6},${r.y + 11} ${labelW + x1},${r.y + 17}`}
                    fill={barColor}
                    fillOpacity={dim ? 0.25 : 1}
                    stroke={crit ? "#b45309" : "#1e293b"}
                    strokeWidth={critOutline ? 1.5 : 1}
                  >
                    <title>
                      {a.activity_code} {a.name}
                      {"\n"}
                      {a.start_date}
                    </title>
                  </polygon>
                ) : (
                  <>
                    <rect
                      x={labelW + x1}
                      y={r.y + 4}
                      width={w}
                      height={14}
                      fill={barColor}
                      fillOpacity={barOpacity}
                      stroke={critOutline ? "#b45309" : "none"}
                      strokeWidth={critOutline ? 1.5 : 0}
                      rx={2}
                    >
                      <title>
                        {a.activity_code} {a.name}
                        {"\n"}Start: {a.start_date} · Finish: {a.finish_date}
                        {"\n"}Duration:{" "}
                        {a.expected_wd == null
                          ? "—"
                          : Number(a.expected_wd).toFixed(1)}{" "}
                        wd · Float: {float} wd
                        {crit && "\nON CRITICAL PATH"}
                      </title>
                    </rect>
                    {(() => {
                      // What actually happened: a thin green bar under the
                      // plan, open-ended to today while still under way.
                      const act = actuals[a.activity_code];
                      if (!act?.actual_start) return null;
                      const ax1 = view.xOf(act.actual_start);
                      const end = act.actual_finish ?? new Date().toISOString().slice(0, 10);
                      const ax2 = view.xOf(end);
                      return (
                        <rect
                          x={labelW + ax1}
                          y={r.y + 18}
                          width={Math.max(3, ax2 - ax1)}
                          height={4}
                          fill="#15803d"
                          fillOpacity={act.actual_finish ? 0.95 : 0.55}
                          rx={1}
                        >
                          <title>
                            Actual: started {act.actual_start}
                            {act.actual_finish ? ` · finished ${act.actual_finish}` : ` · ${act.percent_complete ?? 0}% so far`}
                            {act.from_draft ? "\n(from a draft site report)" : ""}
                          </title>
                        </rect>
                      );
                    })()}
                    {a.id &&
                      (view.inspByAct.get(a.id) ?? []).map((insp, k) => (
                        <circle
                          key={k}
                          cx={labelW + x2}
                          cy={r.y + 11}
                          r={3}
                          fill="#dc2626"
                          stroke="#fef2f2"
                          strokeWidth={1}
                        >
                          <title>
                            Inspection: {insp.authority} —{" "}
                            {insp.inspection_type}
                          </title>
                        </circle>
                      ))}
                  </>
                )}
              </g>
            );
          })}
        </svg>

        {/* Drag handle for the label-column divider. Sits over the SVG
            gutter line; only visible on hover for cleanliness. Pointer
            events captured globally in useEffect above. */}
        <div
          onPointerDown={startDrag}
          className="absolute top-0 bottom-0 group"
          style={{
            left: labelW - 3,
            width: 6,
            cursor: "col-resize",
            zIndex: 5,
          }}
          title="Drag to resize the task column"
        >
          <div
            className="h-full w-full bg-transparent group-hover:bg-blue-200/40"
            style={{ transition: "background-color 80ms" }}
          />
        </div>
      </div>

      {/* Legend */}
      <div className="px-4 py-2 border-t border-slate-200 flex gap-6 text-[10px] text-slate-600 flex-wrap">
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-3 rounded-sm"
            style={{ background: "#3b82f6" }}
          />
          Activity
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-3 rounded-sm"
            style={{ background: "#f59e0b" }}
          />
          On critical path
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block"
            style={{
              width: 0,
              height: 0,
              borderLeft: "5px solid transparent",
              borderRight: "5px solid transparent",
              borderBottom: "8px solid #1e293b",
            }}
          />
          Milestone
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-2 h-2 rounded-full"
            style={{ background: "#dc2626" }}
          />
          AHJ inspection
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-[2px]"
            style={{ background: "#0ea5e9" }}
          />
          Today
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-[2px]"
            style={{
              background:
                "repeating-linear-gradient(90deg, #94a3b8, #94a3b8 2px, transparent 2px, transparent 4px)",
            }}
          />
          Project start
        </span>
      </div>
    </div>
  );
}
