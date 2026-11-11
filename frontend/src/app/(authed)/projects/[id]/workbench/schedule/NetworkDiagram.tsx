"use client";

import { useMemo, useState } from "react";

// AON node uses these fields plus predecessor_codes for the link graph.
type Activity = {
  id?: string;
  activity_code: string;
  name: string;
  phase: string;
  predecessor_codes?: string[];
  expected_wd: number | string | null;
  early_start_wd: number | string | null;
  early_finish_wd: number | string | null;
  late_start_wd: number | string | null;
  late_finish_wd: number | string | null;
  total_float_wd: number | null;
  is_critical: boolean;
  is_milestone: boolean;
};

const num = (v: number | string | null | undefined, digits = 0) => {
  if (v == null || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
};

const BOX_W = 168;
const BOX_H = 90;
const COL_GAP = 56;
const ROW_GAP = 24;
const PAD = 40;

/**
 * Activity-on-Node Precedence Diagram.
 *
 * Layout: each activity's level = max(level of predecessors) + 1, so
 * predecessors always sit to the left of their successors. Within a
 * level, nodes are sorted by phase + code and stacked vertically.
 *
 * Arrows: cubic Bezier from each activity's predecessor → activity.
 * Critical-chain arrows (both endpoints critical) render in amber with
 * a thicker stroke. Critical-path nodes get an amber outline + faint
 * amber fill so the chain visually pops without the chart turning into
 * a wall of colour.
 */
export function NetworkDiagram({
  activities,
  criticalCodes,
}: {
  activities: Activity[];
  criticalCodes: string[];
}) {
  const cpSet = useMemo(() => new Set(criticalCodes), [criticalCodes]);
  const [hoverCode, setHoverCode] = useState<string | null>(null);
  const [showOnlyCritical, setShowOnlyCritical] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  // Build the layout once per activity-list change.
  const view = useMemo(() => {
    if (activities.length === 0) return null;

    const byCode = new Map<string, Activity>();
    for (const a of activities) byCode.set(a.activity_code, a);

    // Topological level — walk predecessors, memoize. Tolerates missing
    // predecessor codes (treats them as level -1).
    const levelCache = new Map<string, number>();
    const stack = new Set<string>();
    const levelOf = (code: string): number => {
      if (levelCache.has(code)) return levelCache.get(code)!;
      if (stack.has(code)) return 0; // cycle guard
      stack.add(code);
      const a = byCode.get(code);
      const preds = a?.predecessor_codes ?? [];
      let max = -1;
      for (const p of preds) {
        if (!byCode.has(p)) continue;
        max = Math.max(max, levelOf(p));
      }
      const lvl = max + 1;
      stack.delete(code);
      levelCache.set(code, lvl);
      return lvl;
    };
    for (const a of activities) levelOf(a.activity_code);

    // Group by level
    const byLevel = new Map<number, Activity[]>();
    let maxLevel = 0;
    for (const a of activities) {
      const l = levelCache.get(a.activity_code) ?? 0;
      if (l > maxLevel) maxLevel = l;
      if (!byLevel.has(l)) byLevel.set(l, []);
      byLevel.get(l)!.push(a);
    }

    // Sort within each level: phase (preserve first-seen project order),
    // then activity code so the layout is stable.
    const phaseOrder = new Map<string, number>();
    for (const a of activities) {
      if (!phaseOrder.has(a.phase)) phaseOrder.set(a.phase, phaseOrder.size);
    }
    for (const list of byLevel.values()) {
      list.sort((x, y) => {
        const px = phaseOrder.get(x.phase) ?? 999;
        const py = phaseOrder.get(y.phase) ?? 999;
        if (px !== py) return px - py;
        return x.activity_code.localeCompare(y.activity_code);
      });
    }

    // Position every node: x by level, y by intra-level index.
    const pos = new Map<string, { x: number; y: number; lvl: number }>();
    let maxRowsPerLevel = 0;
    for (let l = 0; l <= maxLevel; l++) {
      const list = byLevel.get(l) ?? [];
      maxRowsPerLevel = Math.max(maxRowsPerLevel, list.length);
      list.forEach((a, i) => {
        pos.set(a.activity_code, {
          x: PAD + l * (BOX_W + COL_GAP),
          y: PAD + i * (BOX_H + ROW_GAP),
          lvl: l,
        });
      });
    }
    const TOTAL_W = PAD + (maxLevel + 1) * (BOX_W + COL_GAP);
    const TOTAL_H = PAD + maxRowsPerLevel * (BOX_H + ROW_GAP);

    // Edges — every (pred, activity) where pred exists in the graph.
    const edges: {
      from: string;
      to: string;
      critical: boolean;
      path: string;
    }[] = [];
    for (const a of activities) {
      const aPos = pos.get(a.activity_code);
      if (!aPos) continue;
      const aCrit = cpSet.has(a.activity_code) || a.is_critical;
      for (const pcode of a.predecessor_codes ?? []) {
        const pPos = pos.get(pcode);
        const pAct = byCode.get(pcode);
        if (!pPos || !pAct) continue;
        const pCrit = cpSet.has(pcode) || pAct.is_critical;
        const critical = aCrit && pCrit;
        const x1 = pPos.x + BOX_W;
        const y1 = pPos.y + BOX_H / 2;
        const x2 = aPos.x;
        const y2 = aPos.y + BOX_H / 2;
        // Cubic Bezier with control points pushed out horizontally
        // proportional to the level gap, so multi-level jumps curve
        // gracefully around intermediate nodes.
        const cpDist = Math.max(40, (x2 - x1) * 0.45);
        const path = `M ${x1} ${y1} C ${x1 + cpDist} ${y1}, ${x2 - cpDist} ${y2}, ${x2} ${y2}`;
        edges.push({
          from: pcode,
          to: a.activity_code,
          critical,
          path,
        });
      }
    }

    return { pos, edges, maxLevel, TOTAL_W, TOTAL_H };
  }, [activities, cpSet]);

  if (!view) {
    return (
      <div className="text-sm text-slate-500 italic py-8 text-center border border-slate-200 bg-white">
        No activities to diagram
      </div>
    );
  }

  return (
    <div className="border border-slate-200 bg-white">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 px-3 py-2 text-xs">
        <button
          type="button"
          onClick={() => setShowOnlyCritical((v) => !v)}
          className={`px-2 py-1 rounded border text-xs font-medium ${
            showOnlyCritical
              ? "bg-amber-500 text-white border-amber-500"
              : "bg-white text-slate-700 border-slate-300 hover:bg-slate-50"
          }`}
          title="Dim non-critical nodes and links"
        >
          Critical path {showOnlyCritical ? "on" : "off"}
        </button>
        <span className="ml-auto text-[10px] text-slate-400">
          {activities.length} nodes · {view.edges.length} links ·{" "}
          {view.maxLevel + 1} levels
        </span>
        <button
          type="button"
          onClick={() => setHelpOpen((v) => !v)}
          className="px-2 py-1 rounded border border-slate-300 bg-white text-xs font-medium text-slate-700 hover:bg-slate-50"
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
              An Activity-on-Node (AON) precedence diagram. Every activity
              is a box, every arrow is a &ldquo;must-finish-before&rdquo;
              relationship. The critical path is the longest chain through
              this network — any delay on any node along it delays the
              whole project.
            </p>
          </div>
          <div>
            <div className="font-semibold text-slate-800 mb-1">
              How to read a node (box)
            </div>
            <div className="my-2 flex gap-3 items-start">
              <svg width="170" height="92" className="flex-shrink-0">
                <rect
                  x={1}
                  y={1}
                  width={168}
                  height={90}
                  fill="#fff"
                  stroke="#94a3b8"
                  rx={2}
                />
                <line x1={0} y1={22} x2={170} y2={22} stroke="#cbd5e1" />
                <line x1={56} y1={0} x2={56} y2={22} stroke="#e2e8f0" />
                <line x1={113} y1={0} x2={113} y2={22} stroke="#e2e8f0" />
                <text x={28} y={15} fontSize="9.5" textAnchor="middle" fill="#64748b">
                  ES 3
                </text>
                <text
                  x={85}
                  y={15}
                  fontSize="9.5"
                  textAnchor="middle"
                  fontWeight={600}
                  fill="#0f172a"
                >
                  Dur 5
                </text>
                <text x={142} y={15} fontSize="9.5" textAnchor="middle" fill="#64748b">
                  EF 8
                </text>
                <text x={85} y={42} fontSize="10" textAnchor="middle" fontWeight={700} fill="#1e293b">
                  A05
                </text>
                <text x={85} y={57} fontSize="9" textAnchor="middle" fill="#475569">
                  Door framing
                </text>
                <line x1={0} y1={68} x2={170} y2={68} stroke="#cbd5e1" />
                <line x1={56} y1={68} x2={56} y2={90} stroke="#e2e8f0" />
                <line x1={113} y1={68} x2={113} y2={90} stroke="#e2e8f0" />
                <text x={28} y={83} fontSize="9.5" textAnchor="middle" fill="#64748b">
                  LS 3
                </text>
                <text x={85} y={83} fontSize="9.5" textAnchor="middle" fontWeight={600} fill="#0f172a">
                  TF 0
                </text>
                <text x={142} y={83} fontSize="9.5" textAnchor="middle" fill="#64748b">
                  LF 8
                </text>
              </svg>
              <ul className="list-disc list-inside space-y-0.5">
                <li>
                  <strong>Top row</strong>: ES (Early Start) · Dur (Duration
                  in working days) · EF (Early Finish)
                </li>
                <li>
                  <strong>Middle</strong>: Activity code and short name
                </li>
                <li>
                  <strong>Bottom row</strong>: LS (Late Start) · TF (Total
                  Float) · LF (Late Finish)
                </li>
                <li>
                  When <strong>TF = 0</strong> the node is on the critical
                  path. ES = LS and EF = LF — there&rsquo;s no slack.
                </li>
              </ul>
            </div>
          </div>
          <div>
            <div className="font-semibold text-slate-800 mb-1">
              How to read the arrows
            </div>
            <ul className="list-disc list-inside space-y-0.5">
              <li>
                Each arrow goes from a <strong>predecessor</strong> to a{" "}
                <strong>successor</strong>: the head node can&rsquo;t start
                until the tail node finishes.
              </li>
              <li>
                <span className="inline-block w-5 h-[1.5px] bg-amber-700 align-middle mr-1.5" />
                Thicker amber arrows = critical-path links (both endpoints
                are critical).
              </li>
              <li>
                <span className="inline-block w-5 h-[1px] bg-slate-400 align-middle mr-1.5" />
                Thin grey arrows = ordinary logic links.
              </li>
            </ul>
          </div>
          <div>
            <div className="font-semibold text-slate-800 mb-1">Layout</div>
            <p>
              Columns left-to-right are topological levels — every node sits
              one column to the right of its latest predecessor. Two nodes in
              the same column have no precedence relationship to each other,
              so they could run in parallel.
            </p>
          </div>
          <div>
            <div className="font-semibold text-slate-800 mb-1">Controls</div>
            <ul className="list-disc list-inside space-y-0.5">
              <li>
                <strong>Critical path</strong> toggle dims non-critical nodes
                and links so the critical chain stands out.
              </li>
              <li>
                Hover any node for full CPM details, including phase and
                duration.
              </li>
              <li>The diagram scrolls in both directions for wide projects.</li>
            </ul>
          </div>
        </div>
      )}

      <div className="overflow-auto" style={{ maxHeight: "75vh" }}>
        <svg
          width={view.TOTAL_W}
          height={view.TOTAL_H}
          className="block"
          style={{ fontFamily: "ui-sans-serif, system-ui, sans-serif" }}
        >
          {/* Edge arrowhead defs */}
          <defs>
            <marker
              id="arrow-grey"
              viewBox="0 0 10 10"
              refX={9}
              refY={5}
              markerWidth={6}
              markerHeight={6}
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
            </marker>
            <marker
              id="arrow-amber"
              viewBox="0 0 10 10"
              refX={9}
              refY={5}
              markerWidth={7}
              markerHeight={7}
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#b45309" />
            </marker>
          </defs>

          {/* Edges — render first so nodes sit on top */}
          {view.edges.map((e, i) => {
            const dim =
              (showOnlyCritical && !e.critical) ||
              (hoverCode != null &&
                hoverCode !== e.from &&
                hoverCode !== e.to);
            return (
              <path
                key={i}
                d={e.path}
                fill="none"
                stroke={e.critical ? "#b45309" : "#94a3b8"}
                strokeWidth={e.critical ? 1.8 : 1}
                strokeOpacity={dim ? 0.12 : e.critical ? 0.95 : 0.6}
                markerEnd={
                  e.critical ? "url(#arrow-amber)" : "url(#arrow-grey)"
                }
              />
            );
          })}

          {/* Nodes */}
          {activities.map((a) => {
            const p = view.pos.get(a.activity_code);
            if (!p) return null;
            const crit = cpSet.has(a.activity_code) || a.is_critical;
            const dim = showOnlyCritical && !crit;
            const isHover = hoverCode === a.activity_code;
            const fill = a.is_milestone
              ? "#0f172a"
              : crit
                ? "#fef3c7"
                : "#ffffff";
            const stroke = a.is_milestone
              ? "#0f172a"
              : crit
                ? "#b45309"
                : "#cbd5e1";

            // Name truncate
            const maxChars = 22;
            const displayName =
              a.name.length > maxChars
                ? a.name.slice(0, maxChars - 1) + "…"
                : a.name;

            return (
              <g
                key={a.activity_code}
                onMouseEnter={() => setHoverCode(a.activity_code)}
                onMouseLeave={() => setHoverCode(null)}
                opacity={dim ? 0.22 : isHover ? 1 : 1}
                style={{ cursor: "default" }}
              >
                <title>
                  {a.activity_code} — {a.name}
                  {"\n"}Phase: {a.phase}
                  {"\n"}Dur {num(a.expected_wd, 1)} wd · TF{" "}
                  {a.total_float_wd ?? "—"} wd
                  {"\n"}ES {num(a.early_start_wd)} · EF{" "}
                  {num(a.early_finish_wd)} · LS {num(a.late_start_wd)} · LF{" "}
                  {num(a.late_finish_wd)}
                  {crit ? "\nON CRITICAL PATH" : ""}
                </title>
                {/* Box */}
                <rect
                  x={p.x}
                  y={p.y}
                  width={BOX_W}
                  height={BOX_H}
                  fill={fill}
                  stroke={stroke}
                  strokeWidth={crit ? 1.5 : 1}
                  rx={2}
                />
                {/* Top row: ES | Dur | EF */}
                <line
                  x1={p.x}
                  y1={p.y + 22}
                  x2={p.x + BOX_W}
                  y2={p.y + 22}
                  stroke={stroke}
                  strokeWidth={0.8}
                  opacity={0.6}
                />
                <line
                  x1={p.x + BOX_W / 3}
                  y1={p.y}
                  x2={p.x + BOX_W / 3}
                  y2={p.y + 22}
                  stroke={stroke}
                  strokeWidth={0.5}
                  opacity={0.5}
                />
                <line
                  x1={p.x + (BOX_W * 2) / 3}
                  y1={p.y}
                  x2={p.x + (BOX_W * 2) / 3}
                  y2={p.y + 22}
                  stroke={stroke}
                  strokeWidth={0.5}
                  opacity={0.5}
                />
                <text
                  x={p.x + BOX_W / 6}
                  y={p.y + 15}
                  fontSize="9.5"
                  fill={a.is_milestone ? "#fff" : "#64748b"}
                  textAnchor="middle"
                >
                  ES {num(a.early_start_wd)}
                </text>
                <text
                  x={p.x + BOX_W / 2}
                  y={p.y + 15}
                  fontSize="9.5"
                  fontWeight={600}
                  fill={a.is_milestone ? "#fff" : "#0f172a"}
                  textAnchor="middle"
                >
                  Dur {num(a.expected_wd, 1)}
                </text>
                <text
                  x={p.x + (BOX_W * 5) / 6}
                  y={p.y + 15}
                  fontSize="9.5"
                  fill={a.is_milestone ? "#fff" : "#64748b"}
                  textAnchor="middle"
                >
                  EF {num(a.early_finish_wd)}
                </text>

                {/* Middle: code + name */}
                <text
                  x={p.x + BOX_W / 2}
                  y={p.y + 40}
                  fontSize="10"
                  fontWeight={700}
                  fill={a.is_milestone ? "#fff" : "#1e293b"}
                  textAnchor="middle"
                >
                  {a.activity_code}
                </text>
                <text
                  x={p.x + BOX_W / 2}
                  y={p.y + 55}
                  fontSize="9"
                  fill={a.is_milestone ? "#fff" : "#475569"}
                  textAnchor="middle"
                >
                  {displayName}
                </text>

                {/* Bottom row: LS | TF | LF */}
                <line
                  x1={p.x}
                  y1={p.y + 66}
                  x2={p.x + BOX_W}
                  y2={p.y + 66}
                  stroke={stroke}
                  strokeWidth={0.8}
                  opacity={0.6}
                />
                <line
                  x1={p.x + BOX_W / 3}
                  y1={p.y + 66}
                  x2={p.x + BOX_W / 3}
                  y2={p.y + BOX_H}
                  stroke={stroke}
                  strokeWidth={0.5}
                  opacity={0.5}
                />
                <line
                  x1={p.x + (BOX_W * 2) / 3}
                  y1={p.y + 66}
                  x2={p.x + (BOX_W * 2) / 3}
                  y2={p.y + BOX_H}
                  stroke={stroke}
                  strokeWidth={0.5}
                  opacity={0.5}
                />
                <text
                  x={p.x + BOX_W / 6}
                  y={p.y + 81}
                  fontSize="9.5"
                  fill={a.is_milestone ? "#fff" : "#64748b"}
                  textAnchor="middle"
                >
                  LS {num(a.late_start_wd)}
                </text>
                <text
                  x={p.x + BOX_W / 2}
                  y={p.y + 81}
                  fontSize="9.5"
                  fontWeight={600}
                  fill={
                    a.is_milestone
                      ? "#fff"
                      : crit
                        ? "#b45309"
                        : "#0f172a"
                  }
                  textAnchor="middle"
                >
                  TF {a.total_float_wd ?? "—"}
                </text>
                <text
                  x={p.x + (BOX_W * 5) / 6}
                  y={p.y + 81}
                  fontSize="9.5"
                  fill={a.is_milestone ? "#fff" : "#64748b"}
                  textAnchor="middle"
                >
                  LF {num(a.late_finish_wd)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Legend */}
      <div className="px-4 py-2 border-t border-slate-200 flex gap-6 text-[10px] text-slate-600 flex-wrap">
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-3 border border-slate-300 bg-white"
            style={{ borderRadius: 1 }}
          />
          Activity node
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-3 border-2 bg-amber-100"
            style={{ borderRadius: 1, borderColor: "#b45309" }}
          />
          On critical path
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-3"
            style={{ background: "#0f172a", borderRadius: 1 }}
          />
          Milestone
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-5 h-[1.5px] bg-amber-700" />
          Critical link
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-5 h-[1px] bg-slate-400" />
          Logic link
        </span>
        <span className="ml-auto text-slate-400">
          Hover a node for full details · ES/EF/LS/LF/TF in working days
        </span>
      </div>
    </div>
  );
}
