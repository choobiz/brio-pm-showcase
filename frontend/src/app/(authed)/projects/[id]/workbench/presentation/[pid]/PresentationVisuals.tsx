"use client";

import { useState } from "react";

const PRIMARY = "#0068A0";
const ACCENT = "#1D3146";
const PALETTE = ["#0068A0", "#1D3146", "#4A90C2", "#2E5E7E", "#7FB2D6", "#375A70", "#A9CBE3", "#5C7A90", "#C9DDEB"];

const money = (n: number) => `$${Math.round(n || 0).toLocaleString()}`;

function topSegments(rows: { name: string; value: number }[], keep = 8) {
  const clean = rows.filter((r) => (r.value || 0) > 0).sort((a, b) => b.value - a.value);
  if (clean.length <= keep) return clean;
  const other = clean.slice(keep).reduce((s, r) => s + r.value, 0);
  return [...clean.slice(0, keep), { name: "Other", value: other }];
}

/** Interactive budget donut + legend (hover a segment or row to focus it). */
export function BudgetChart({ categories }: { categories: { name: string; value: number }[] }) {
  const [hi, setHi] = useState<number | null>(null);
  const segments = topSegments(categories);
  const total = segments.reduce((s, x) => s + Math.max(0, x.value), 0) || 1;
  const maxV = Math.max(...segments.map((s) => s.value), 1);
  const size = 190, thick = 34, r = (size - thick) / 2, circ = 2 * Math.PI * r, cx = size / 2, cy = size / 2;
  let off = 0;

  return (
    <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
      <div className="flex-none">
        <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-slate-400">Where the budget goes</div>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Budget by trade">
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="#eef1f4" strokeWidth={thick} />
          {segments.map((s, i) => {
            const dash = (Math.max(0, s.value) / total) * circ;
            const node = (
              <circle
                key={i} cx={cx} cy={cy} r={r} fill="none"
                stroke={PALETTE[i % PALETTE.length]}
                strokeWidth={hi === i ? thick + 5 : thick}
                strokeDasharray={`${dash} ${circ - dash}`} strokeDashoffset={-off}
                transform={`rotate(-90 ${cx} ${cy})`}
                opacity={hi === null || hi === i ? 1 : 0.3}
                style={{ cursor: "pointer", transition: "opacity .12s, stroke-width .12s" }}
                onMouseEnter={() => setHi(i)} onMouseLeave={() => setHi(null)}
              />
            );
            off += dash;
            return node;
          })}
          <text x={cx} y={cy - 1} textAnchor="middle" fontFamily="Sora, sans-serif" fontSize={17} fontWeight={700} fill={ACCENT}>
            {hi === null ? money(total) : money(segments[hi].value)}
          </text>
          <text x={cx} y={cy + 15} textAnchor="middle" fontSize={8} letterSpacing={1} fill="#8a94a0">
            {hi === null ? "SUBTOTAL" : `${Math.round((100 * segments[hi].value) / total)}%`}
          </text>
        </svg>
      </div>
      <div className="min-w-[220px] flex-1 space-y-0.5">
        {segments.map((s, i) => (
          <div
            key={i}
            className="flex items-center gap-2 rounded px-1.5 py-1 text-sm transition-colors"
            style={{ background: hi === i ? "#eef5fa" : "transparent" }}
            onMouseEnter={() => setHi(i)} onMouseLeave={() => setHi(null)}
          >
            <span className="inline-block h-3 w-3 flex-none" style={{ background: PALETTE[i % PALETTE.length] }} />
            <span className="w-40 flex-none truncate text-slate-700">{s.name}</span>
            <span className="h-2 flex-1" style={{ background: "#f0f3f6" }}>
              <span className="block h-2" style={{ width: `${(s.value / maxV) * 100}%`, background: PALETTE[i % PALETTE.length] }} />
            </span>
            <span className="w-24 flex-none whitespace-nowrap text-right text-slate-500">
              {money(s.value)} · {Math.round((100 * s.value) / total)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

type Activity = {
  code?: string; name?: string; phase?: string; duration?: number;
  start?: string | null; finish?: string | null; critical?: boolean; milestone?: boolean;
};

const parse = (d?: string | null) => (d ? new Date(d + "T00:00:00") : null);

/** Interactive Gantt — hover a bar for task/dates/duration. Critical path in primary, other work in tint. */
export function ScheduleGantt({
  activities, milestones, start, finish,
}: {
  activities: Activity[];
  milestones?: { name?: string; date?: string | null }[];
  start?: string; finish?: string;
}) {
  const [hi, setHi] = useState<number | null>(null);
  const bars = activities.filter((a) => !a.milestone && parse(a.start));
  const dates = [
    parse(start), parse(finish),
    ...bars.flatMap((b) => [parse(b.start), parse(b.finish)]),
    ...(milestones || []).map((m) => parse(m.date)),
  ].filter(Boolean) as Date[];
  if (!dates.length) return null;
  const lo = new Date(Math.min(...dates.map((d) => +d)));
  const hiD = new Date(Math.max(...dates.map((d) => +d)));
  const span = Math.max((+hiD - +lo) / 86400000, 1);
  const W = 720, left = 168, padR = 16, plotW = W - left - padR;
  const x = (d: Date) => left + ((+d - +lo) / 86400000 / span) * plotW;
  const rowH = 17, gap = 4, top = 22;
  const H = top + bars.length * (rowH + gap) + 34;

  // month gridlines
  const months: Date[] = [];
  const cur = new Date(lo.getFullYear(), lo.getMonth(), 1);
  while (cur <= hiD) { months.push(new Date(cur)); cur.setMonth(cur.getMonth() + 1); }

  return (
    <div className="overflow-x-auto">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ fontFamily: "inherit" }}>
        {months.map((m, i) => {
          const gx = x(m < lo ? lo : m);
          return (
            <g key={i}>
              <line x1={gx} y1={top - 4} x2={gx} y2={H - 26} stroke="#eef1f4" />
              <text x={gx + 2} y={top - 8} fontSize={8} fill="#9aa3ad">
                {m.toLocaleDateString("en-US", { month: "short", year: "numeric" })}
              </text>
            </g>
          );
        })}
        {bars.map((b, i) => {
          const s = parse(b.start)!, f = parse(b.finish) || s;
          const bx = x(s), bw = Math.max(3, x(f) - bx), y = top + i * (rowH + gap);
          const active = hi === i;
          return (
            <g key={i} onMouseEnter={() => setHi(i)} onMouseLeave={() => setHi(null)} style={{ cursor: "pointer" }}>
              <rect x={0} y={y - 1} width={W} height={rowH} fill={active ? "#f4f8fb" : "transparent"} />
              <text x={0} y={y + rowH - 5} fontSize={8} fill="#3f4a56">{(b.name || "").slice(0, 30)}</text>
              <rect x={bx} y={y} width={bw} height={rowH - 4} fill={b.critical ? PRIMARY : "#A9CBE3"} />
            </g>
          );
        })}
        {(milestones || []).map((m, i) => {
          const d = parse(m.date); if (!d) return null;
          const mx = x(d), my = H - 18;
          return <path key={i} d={`M${mx} ${my - 5} L${mx + 5} ${my} L${mx} ${my + 5} L${mx - 5} ${my} Z`} fill={ACCENT} />;
        })}
      </svg>
      <div className="mt-1 flex gap-5 text-xs text-slate-500">
        <span className="flex items-center gap-1.5"><i className="inline-block h-2 w-4" style={{ background: PRIMARY }} />Critical path</span>
        <span className="flex items-center gap-1.5"><i className="inline-block h-2 w-4" style={{ background: "#A9CBE3" }} />Other work</span>
        <span className="flex items-center gap-1.5"><i className="inline-block h-2 w-2 rotate-45" style={{ background: ACCENT }} />Milestone</span>
      </div>
      {hi !== null && bars[hi] && (
        <div className="mt-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs shadow-sm">
          <span className="font-semibold text-slate-800">{bars[hi].name}</span>
          <span className="text-slate-500">
            {" — "}{bars[hi].phase} · {Math.round(bars[hi].duration || 0)} working days · {bars[hi].start} → {bars[hi].finish}
            {bars[hi].critical ? " · critical path" : ""}
          </span>
        </div>
      )}
    </div>
  );
}
