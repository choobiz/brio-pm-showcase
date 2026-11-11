"use client";

import { useState } from "react";
import { fmtDate, fmtMoney } from "@/lib/fmt";

type Week = {
  week_num: number;
  week_end: string;
  cumulative_position: number | null;
};

/**
 * Dependency-free SVG line chart of the cumulative cash position across the
 * project's weeks. Shows the zero baseline, shades the below-zero region, and
 * marks the trough (min position). Hover reveals the week's exact figure.
 */
export function CashPositionChart({ periods }: { periods: Week[] }) {
  const [hover, setHover] = useState<number | null>(null);

  const pts = periods.map((w) => ({
    ...w,
    y: Number(w.cumulative_position ?? 0),
  }));
  if (pts.length < 2) return null;

  // Geometry (viewBox units; SVG scales responsively to the container width).
  const W = 860;
  const H = 300;
  const padL = 76;
  const padR = 18;
  const padT = 18;
  const padB = 40;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const ys = pts.map((p) => p.y);
  const rawMin = Math.min(0, ...ys);
  const rawMax = Math.max(0, ...ys);
  const span = rawMax - rawMin || 1;
  const pad = span * 0.08;
  const domMin = rawMin - pad;
  const domMax = rawMax + pad;

  const x = (i: number) => padL + (plotW * i) / (pts.length - 1);
  const y = (v: number) => padT + plotH * (1 - (v - domMin) / (domMax - domMin));

  const zeroY = y(0);
  const linePath = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(p.y)}`).join(" ");
  // Area down to the zero baseline (fill both above/below; colored via two clips).
  const areaPath =
    `M ${x(0)} ${zeroY} ` +
    pts.map((p, i) => `L ${x(i)} ${y(p.y)}`).join(" ") +
    ` L ${x(pts.length - 1)} ${zeroY} Z`;

  // Trough (most negative / lowest) week.
  let troughIdx = 0;
  for (let i = 1; i < pts.length; i++) if (pts[i].y < pts[troughIdx].y) troughIdx = i;

  const yTicks = [domMax, (domMax + domMin) / 2, domMin, 0]
    .filter((v, i, a) => a.indexOf(v) === i);

  // x-axis labels: first, quartiles, last.
  const xLabelIdx = [0, Math.floor(pts.length / 3), Math.floor((2 * pts.length) / 3), pts.length - 1]
    .filter((v, i, a) => a.indexOf(v) === i);

  const active = hover != null ? pts[hover] : null;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto select-none"
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <clipPath id="cf-above">
            <rect x={padL} y={padT} width={plotW} height={Math.max(0, zeroY - padT)} />
          </clipPath>
          <clipPath id="cf-below">
            <rect x={padL} y={zeroY} width={plotW} height={Math.max(0, padT + plotH - zeroY)} />
          </clipPath>
        </defs>

        {/* y gridlines + labels */}
        {yTicks.map((v, i) => (
          <g key={i}>
            <line
              x1={padL}
              x2={W - padR}
              y1={y(v)}
              y2={y(v)}
              stroke={Math.abs(v) < 1e-6 ? "#94a3b8" : "#eef1f4"}
              strokeWidth={Math.abs(v) < 1e-6 ? 1 : 1}
              strokeDasharray={Math.abs(v) < 1e-6 ? "4 3" : undefined}
            />
            <text x={padL - 8} y={y(v) + 3} textAnchor="end" fontSize="11" fill="#8a94a0">
              {fmtMoney(v)}
            </text>
          </g>
        ))}

        {/* area fills — emerald above zero, red below */}
        <path d={areaPath} fill="#10b98122" clipPath="url(#cf-above)" />
        <path d={areaPath} fill="#ef444422" clipPath="url(#cf-below)" />

        {/* the cash-position line */}
        <path d={linePath} fill="none" stroke="#0068A0" strokeWidth="2" />

        {/* trough marker */}
        <circle cx={x(troughIdx)} cy={y(pts[troughIdx].y)} r="4" fill="#dc2626" />
        <text
          x={x(troughIdx)}
          y={y(pts[troughIdx].y) + 18}
          textAnchor="middle"
          fontSize="10.5"
          fill="#b91c1c"
          fontWeight="600"
        >
          trough {fmtMoney(pts[troughIdx].y)}
        </text>

        {/* final position marker */}
        <circle cx={x(pts.length - 1)} cy={y(pts[pts.length - 1].y)} r="4" fill="#059669" />

        {/* x-axis labels */}
        {xLabelIdx.map((i) => (
          <text key={i} x={x(i)} y={H - 14} textAnchor="middle" fontSize="10.5" fill="#8a94a0">
            {fmtDate(pts[i].week_end)}
          </text>
        ))}

        {/* hover hit-targets + marker */}
        {pts.map((p, i) => (
          <rect
            key={i}
            x={x(i) - plotW / (pts.length - 1) / 2}
            y={padT}
            width={plotW / (pts.length - 1)}
            height={plotH}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover((h) => (h === i ? null : h))}
          />
        ))}
        {active && (
          <>
            <line
              x1={x(hover!)}
              x2={x(hover!)}
              y1={padT}
              y2={padT + plotH}
              stroke="#0068A0"
              strokeWidth="1"
              strokeDasharray="3 3"
            />
            <circle cx={x(hover!)} cy={y(active.y)} r="4" fill="#0068A0" />
          </>
        )}
      </svg>

      {active && (
        <div className="absolute top-1 right-2 rounded border border-slate-200 bg-white/95 px-3 py-1.5 text-xs shadow-sm">
          <div className="font-medium text-slate-700">
            Wk {active.week_num} · {fmtDate(active.week_end)}
          </div>
          <div className={active.y < 0 ? "text-red-600" : "text-emerald-700"}>
            Cumulative: {fmtMoney(active.y)}
          </div>
        </div>
      )}
    </div>
  );
}
