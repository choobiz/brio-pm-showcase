"use client";

import type { ReactNode } from "react";

import type { Drift } from "@/lib/rates";

export interface Filters {
  division: string | null;
  trade: string;
  unit: string;
  source: string;
  staleOnly: boolean;
  q: string;
}

export const NO_FILTERS: Filters = { division: null, trade: "", unit: "", source: "", staleOnly: false, q: "" };

export interface Filterable {
  division?: string | null;
  trade_key?: string | null;
  trade_label?: string | null;
  unit?: string;
  source?: string;
  stale?: boolean;
  text: string;
}

export function applyFilters<T>(rows: T[], f: Filters, get: (r: T) => Filterable, withDivision = true): T[] {
  const words = f.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return rows.filter((r) => {
    const x = get(r);
    if (withDivision && f.division && (x.division ?? "—") !== f.division) return false;
    if (f.trade && x.trade_key !== f.trade) return false;
    if (f.unit && x.unit !== f.unit) return false;
    if (f.source && x.source !== f.source) return false;
    if (f.staleOnly && !x.stale) return false;
    return words.every((w) => x.text.toLowerCase().includes(w));
  });
}

export const isStale = (asOf: string | null | undefined, drift?: Drift | null) => !asOf || !!drift?.stale;

/**
 * Wireframe C: filters across the top, divisions down the left with counts, the
 * review tray under them, and the table on the right.
 */
export function LibraryLayout<T>({
  rows,
  get,
  filters,
  setFilters,
  units,
  sources,
  tray,
  children,
}: {
  rows: T[];
  get: (r: T) => Filterable;
  filters: Filters;
  setFilters: (f: Filters) => void;
  units: string[];
  sources: { value: string; label: string }[];
  tray?: ReactNode;
  children: ReactNode;
}) {
  // Division counts honour every filter except the division itself.
  const inScope = applyFilters(rows, filters, get, false);
  const divisions = new Map<string, { n: number; labels: Set<string> }>();
  for (const r of inScope) {
    const x = get(r);
    const d = x.division ?? "—";
    const e = divisions.get(d) ?? { n: 0, labels: new Set<string>() };
    e.n += 1;
    if (x.trade_label) e.labels.add(x.trade_label);
    divisions.set(d, e);
  }
  const trades = new Map<string, string>();
  for (const r of rows) {
    const x = get(r);
    if (x.trade_key) trades.set(x.trade_key, x.trade_label ?? x.trade_key);
  }
  const set = (p: Partial<Filters>) => setFilters({ ...filters, ...p });
  const sel = "rounded border border-slate-300 px-2 py-1 text-sm";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-xs text-slate-500">Filter:</span>
        <select value={filters.trade} onChange={(e) => set({ trade: e.target.value })} className={sel} aria-label="Trade">
          <option value="">All trades</option>
          {[...trades].sort((a, b) => a[1].localeCompare(b[1])).map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
        <select value={filters.unit} onChange={(e) => set({ unit: e.target.value })} className={sel} aria-label="Unit">
          <option value="">All units</option>
          {units.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
        <select value={filters.source} onChange={(e) => set({ source: e.target.value })} className={sel} aria-label="Source">
          <option value="">All sources</option>
          {sources.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1 text-xs">
          <input type="checkbox" checked={filters.staleOnly} onChange={(e) => set({ staleOnly: e.target.checked })} />
          only stale prices
        </label>
        <input
          value={filters.q}
          onChange={(e) => set({ q: e.target.value })}
          placeholder="search"
          className="w-48 rounded border border-slate-300 px-2 py-1 text-sm"
        />
      </div>
      <div className="grid gap-4 md:grid-cols-[13rem_minmax(0,1fr)]">
        <nav className="space-y-3 text-sm" aria-label="Divisions">
          <div>
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Divisions</div>
            <button
              type="button"
              onClick={() => set({ division: null })}
              className={`block w-full rounded px-2 py-1 text-left ${filters.division === null ? "bg-slate-900 text-white" : "hover:bg-slate-100"}`}
            >
              All <span className="float-right tabular-nums">{inScope.length}</span>
            </button>
            {[...divisions].sort((a, b) => a[0].localeCompare(b[0])).map(([d, e]) => (
              <button
                key={d}
                type="button"
                onClick={() => set({ division: d })}
                className={`block w-full rounded px-2 py-1 text-left ${filters.division === d ? "bg-slate-900 text-white" : "hover:bg-slate-100"}`}
                title={[...e.labels].join(", ")}
              >
                <span className="mr-6 block truncate">
                  {d === "—" ? "Other" : `${d} ${[...e.labels].sort().join(", ")}`}
                </span>
                <span className="float-right -mt-5 tabular-nums">{e.n}</span>
              </button>
            ))}
          </div>
          {tray}
        </nav>
        <div className="min-w-0">{children}</div>
      </div>
    </div>
  );
}
