"use client";

import { useState } from "react";

import {
  applyImport,
  getImport,
  stageImport,
  type ImportField,
  type ImportRow,
  type PriceImport,
} from "@/lib/rates";

const FIELDS: { key: ImportField; label: string; required: boolean }[] = [
  { key: "sku", label: "SKU / item #", required: true },
  { key: "description", label: "Description", required: true },
  { key: "unit", label: "Unit", required: true },
  { key: "price", label: "Price", required: true },
  { key: "category", label: "Category", required: false },
];

const money = (n: number | null | undefined) =>
  n == null ? "—" : `$${n.toLocaleString("en-CA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/**
 * Import a supplier price sheet (Phase 4, wireframe D, approved 2026-10-16).
 * Upload → match columns → check → import. Nothing is written until the last
 * step; the Imports tab can undo the latest import.
 */
export function ImportWizard({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [supplier, setSupplier] = useState("Renovo");
  const [headers, setHeaders] = useState<string[]>([]);
  const [sample, setSample] = useState<string[][]>([]);
  const [map, setMap] = useState<Partial<Record<ImportField, number>>>({});
  const [staged, setStaged] = useState<PriceImport | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [bucket, setBucket] = useState<{ name: string; rows: ImportRow[]; total: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const step = staged ? 3 : headers.length ? 2 : 1;

  const check = async (columnMap?: Partial<Record<ImportField, number>>) => {
    if (!file || !supplier.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await stageImport(file, supplier.trim(), columnMap);
      setHeaders(r.headers);
      setSample(r.sample);
      if ("status" in r && r.status === "needs_map") {
        setMap(r.column_map);
        setErr(r.detail);
        setStaged(null);
      } else {
        const imp = r as PriceImport;
        setMap(imp.column_map);
        setStaged(imp);
        setTicked(new Set((imp.summary.materials ?? []).filter((m) => m.default).map((m) => m.material_id)));
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const view = async (name: string) => {
    if (!staged) return;
    const r = await getImport(staged.id, name);
    setBucket({ name, rows: r.rows ?? [], total: r.rows_total ?? 0 });
  };

  const run = async () => {
    if (!staged) return;
    setBusy(true);
    setErr(null);
    try {
      await applyImport(staged.id, Array.from(ticked));
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const c = staged?.summary.counts;
  const mats = staged?.summary.materials ?? [];
  const importable = c ? c.new + c.changed + c.unchanged : 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true">
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-md bg-white p-5 shadow-lg">
        <div className="flex items-baseline justify-between">
          <h2 className="m-0 text-lg font-semibold">Import a price sheet</h2>
          <span className="text-xs text-slate-500">Step {step} of 3 · nothing is saved until you import</span>
        </div>

        <div className="mt-3 flex flex-wrap items-end gap-3 text-sm">
          <label className="text-xs text-slate-600">
            Supplier
            <input
              value={supplier}
              onChange={(e) => setSupplier(e.target.value)}
              className="mt-1 block w-48 rounded border border-slate-300 px-2 py-1 text-sm"
            />
          </label>
          <label className="text-xs text-slate-600">
            CSV or Excel sheet (up to 4 MB)
            <input
              type="file"
              accept=".csv,.xlsx,.xlsm,text/csv"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setHeaders([]);
                setStaged(null);
                setBucket(null);
              }}
              className="mt-1 block text-sm"
            />
          </label>
          {step === 1 && (
            <button
              type="button"
              disabled={!file || busy}
              onClick={() => check()}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            >
              {busy ? "Reading…" : "Read the sheet"}
            </button>
          )}
        </div>

        {headers.length > 0 && (
          <section className="mt-4">
            <h3 className="m-0 text-sm font-semibold">Match the columns</h3>
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-5">
              {FIELDS.map((f) => (
                <label key={f.key} className="text-xs text-slate-600">
                  {f.label}
                  {f.required ? "" : " (optional)"}
                  <select
                    value={map[f.key] ?? ""}
                    onChange={(e) =>
                      setMap((m) => {
                        const n = { ...m };
                        if (e.target.value === "") delete n[f.key];
                        else n[f.key] = Number(e.target.value);
                        return n;
                      })
                    }
                    className="mt-1 block w-full rounded border border-slate-300 px-1 py-1 text-xs"
                  >
                    <option value="">—</option>
                    {headers.map((h, i) => (
                      <option key={i} value={i}>
                        {h || `column ${i + 1}`}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
            {sample.length > 0 && (
              <div className="mt-2 overflow-x-auto text-[11px] text-slate-500">
                First row: {FIELDS.filter((f) => map[f.key] != null).map((f) => `${f.label} “${sample[0][map[f.key]!] ?? ""}”`).join(" · ")}
              </div>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setBucket(null);
                check(map);
              }}
              className="mt-2 rounded-md border border-slate-900 px-3 py-1 text-sm"
            >
              {busy ? "Checking…" : staged ? "Re-check with these columns" : "Check the rows"}
            </button>
          </section>
        )}

        {staged && c && (
          <section className="mt-4">
            <h3 className="m-0 text-sm font-semibold">
              Check · {staged.filename} · {staged.summary.total} rows
            </h3>
            <table className="mt-2 w-full text-sm">
              <tbody>
                {(
                  [
                    ["new", "New lines", c.new],
                    ["changed", "Price changed", c.changed],
                    ["unchanged", "Unchanged (date refreshed)", c.unchanged],
                    ["problem", "Problems (skipped)", c.problem],
                  ] as const
                ).map(([k, label, n]) => (
                  <tr key={k} className="border-t border-slate-100">
                    <td className="py-1.5">{label}</td>
                    <td className="py-1.5 text-right tabular-nums">{n.toLocaleString()}</td>
                    <td className="py-1.5 pl-3">
                      {n > 0 && k !== "unchanged" && (
                        <button type="button" className="text-xs text-blue-700" onClick={() => view(k)}>
                          view
                        </button>
                      )}
                      {k === "changed" && c.big_move > 0 && (
                        <button type="button" className="ml-3 text-xs text-orange-800" onClick={() => view("big_move")}>
                          ⚠ {c.big_move} moved more than 25%
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {bucket && (
              <div className="mt-2 max-h-56 overflow-y-auto rounded border border-slate-200">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-slate-50 text-left text-slate-500">
                    <tr>
                      <th className="px-2 py-1">Line</th>
                      <th className="px-2 py-1">SKU</th>
                      <th className="px-2 py-1">Description</th>
                      <th className="px-2 py-1 text-right">Price</th>
                      <th className="px-2 py-1">{bucket.name === "problem" ? "Problem" : "Was"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bucket.rows.map((r) => (
                      <tr key={r.line} className="border-t border-slate-100">
                        <td className="px-2 py-1 text-slate-400">{r.line}</td>
                        <td className="px-2 py-1 font-mono">{r.sku}</td>
                        <td className="px-2 py-1">{r.description}</td>
                        <td className="px-2 py-1 text-right">
                          {typeof r.raw_price === "number" ? money(r.raw_price) : r.raw_price} {r.raw_unit}
                        </td>
                        <td className={`px-2 py-1 ${r.big_move ? "text-orange-800" : "text-slate-500"}`}>
                          {r.problem ??
                            (r.old_price != null
                              ? `${money(r.old_price)} (${(r.change_pct ?? 0) > 0 ? "+" : ""}${r.change_pct}%)`
                              : "")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {bucket.total > bucket.rows.length && (
                  <div className="px-2 py-1 text-[11px] text-slate-500">
                    Showing {bucket.rows.length} of {bucket.total}.
                  </div>
                )}
              </div>
            )}

            {mats.length > 0 && (
              <div className="mt-3">
                <div className="text-[13px] text-slate-600">Library materials that use these SKUs:</div>
                <ul className="m-0 mt-1 list-none p-0 text-sm">
                  {mats.map((m) => (
                    <li key={m.material_id} className="py-0.5">
                      <label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          disabled={m.new_cost == null}
                          checked={ticked.has(m.material_id)}
                          onChange={(e) =>
                            setTicked((t) => {
                              const n = new Set(t);
                              if (e.target.checked) n.add(m.material_id);
                              else n.delete(m.material_id);
                              return n;
                            })
                          }
                        />
                        {m.name} · {money(m.old_cost)} → {m.new_cost == null ? "—" : money(m.new_cost)} /{m.unit}
                        {m.note && <span className="text-xs text-slate-500">({m.note})</span>}
                      </label>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        {err && <p className="mt-3 text-sm text-red-700">{err}</p>}

        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-md border border-slate-400 px-3 py-1.5 text-sm">
            Cancel
          </button>
          {staged && (
            <button
              type="button"
              disabled={busy || importable === 0}
              onClick={run}
              className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            >
              Import {importable.toLocaleString()} lines
              {ticked.size ? ` + ${ticked.size} material${ticked.size === 1 ? "" : "s"}` : ""}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
