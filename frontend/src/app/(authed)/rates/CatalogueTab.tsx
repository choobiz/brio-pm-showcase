"use client";

import { useEffect, useState } from "react";

import { createMaterial, searchCatalogue, type CatalogueLine } from "@/lib/rates";
import { TRADE_CATEGORIES, TRADE_CATEGORY_LABELS } from "@/lib/trades";

import { AgeCell } from "./AgeCell";

const money = (n: number | null | undefined) =>
  n == null ? "—" : `$${n.toLocaleString("en-CA", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;

/** The supplier catalogue: read-only, searchable; a line can become a library material. */
export function CatalogueTab({ onError, onAdded }: { onError: (e: string) => void; onAdded: () => void }) {
  const [q, setQ] = useState("");
  const [trade, setTrade] = useState("");
  const [page, setPage] = useState(0);
  const [res, setRes] = useState<{ total: number; items: CatalogueLine[] } | null>(null);
  const [adding, setAdding] = useState<CatalogueLine | null>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      searchCatalogue({ q: q.trim() || undefined, trade: trade || undefined, limit: 50, offset: page * 50 })
        .then(setRes)
        .catch((e) => onError(String(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [q, trade, page, onError]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <input
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(0);
          }}
          placeholder="Search description or SKU, e.g. 5/8 type x"
          className="w-80 rounded border border-slate-300 px-2 py-1"
        />
        <select
          value={trade}
          onChange={(e) => {
            setTrade(e.target.value);
            setPage(0);
          }}
          className="rounded border border-slate-300 px-2 py-1"
        >
          <option value="">All trades</option>
          {TRADE_CATEGORIES.map((t) => (
            <option key={t} value={t}>
              {TRADE_CATEGORY_LABELS[t]}
            </option>
          ))}
        </select>
        {res && <span className="text-xs text-slate-500">{res.total.toLocaleString()} lines</span>}
      </div>

      {adding && (
        <AddFromCatalogue
          line={adding}
          onCancel={() => setAdding(null)}
          onDone={() => {
            setAdding(null);
            onAdded();
          }}
          onError={onError}
        />
      )}

      <div className="overflow-x-auto rounded border border-slate-200">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">SKU</th>
              <th className="px-3 py-2">Description</th>
              <th className="px-3 py-2 text-right">Price</th>
              <th className="px-3 py-2">Trade</th>
              <th className="px-3 py-2">Age</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {res?.items.map((c) => (
              <tr key={c.id} className="border-t border-slate-100">
                <td className="px-3 py-1.5 font-mono text-xs">{c.sku}</td>
                <td className="px-3 py-1.5">{c.description}</td>
                <td className="px-3 py-1.5 text-right tabular-nums">
                  {money(c.raw_price)} <span className="text-slate-400">{c.raw_unit}</span>
                  {c.unit && c.unit_cost != null && c.unit.toUpperCase() !== c.raw_unit.toUpperCase() && (
                    <div className="text-[11px] text-slate-500">
                      = {money(c.unit_cost)}/{c.unit}
                    </div>
                  )}
                </td>
                <td className="px-3 py-1.5 text-slate-500">{c.trade_label ?? "—"}</td>
                <td className="px-3 py-1.5">
                  <AgeCell asOf={c.as_of_date} drift={c.drift} />
                </td>
                <td className="px-3 py-1.5 text-right">
                  {c.unit ? (
                    <button type="button" className="text-xs text-blue-700" onClick={() => setAdding(c)}>
                      add to library
                    </button>
                  ) : (
                    <span className="text-[11px] text-slate-400" title="The printed unit has no safe reading">
                      unit unread
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {res && res.total > 50 && (
        <div className="flex items-center gap-3 text-sm">
          <button type="button" disabled={page === 0} onClick={() => setPage((p) => p - 1)} className="disabled:opacity-40">
            ← previous
          </button>
          <span className="text-xs text-slate-500">
            {page * 50 + 1}–{Math.min(res.total, (page + 1) * 50)} of {res.total.toLocaleString()}
          </span>
          <button
            type="button"
            disabled={(page + 1) * 50 >= res.total}
            onClick={() => setPage((p) => p + 1)}
            className="disabled:opacity-40"
          >
            next →
          </button>
        </div>
      )}
    </div>
  );
}

const MEASURES = ["", "sf", "lf", "ea", "sy", "cf", "cy"];

/** Turn a catalogue line into a library material: you set what a takeoff measures it in. */
function AddFromCatalogue({
  line,
  onCancel,
  onDone,
  onError,
}: {
  line: CatalogueLine;
  onCancel: () => void;
  onDone: () => void;
  onError: (e: string) => void;
}) {
  const [name, setName] = useState(line.description);
  const [measure, setMeasure] = useState("");
  const [coverage, setCoverage] = useState("");
  const [waste, setWaste] = useState("10");
  const needsCoverage = !!measure && measure !== line.unit;
  const per =
    needsCoverage && Number(coverage) > 0 && line.unit_cost != null
      ? ((line.unit_cost / Number(coverage)) * (1 + (Number(waste) || 0) / 100)).toFixed(2)
      : null;

  const save = async () => {
    try {
      await createMaterial({
        name,
        unit: line.unit!,
        unit_cost: line.unit_cost!,
        source: "supplier",
        supplier: line.supplier,
        sku: line.sku,
        trade: line.trade_key ?? null,
        measure_unit: measure || null,
        coverage: needsCoverage ? Number(coverage) : null,
        waste_pct: Number(waste) || 0,
        as_of_date: line.as_of_date,
        source_ref: `${line.supplier} SKU ${line.sku}`,
      });
      onDone();
    } catch (e) {
      onError(String(e));
    }
  };

  return (
    <div className="rounded border border-slate-300 bg-slate-50 p-3 text-sm">
      <div className="font-medium">
        Add to library: <span className="font-mono text-xs">{line.sku}</span> · ${line.unit_cost}/{line.unit}
      </div>
      <div className="mt-2 flex flex-wrap items-end gap-3">
        <label className="text-xs text-slate-600">
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} className="mt-1 block w-72 rounded border border-slate-300 px-2 py-1" />
        </label>
        <label className="text-xs text-slate-600">
          Takeoff measures it in
          <select value={measure} onChange={(e) => setMeasure(e.target.value)} className="mt-1 block rounded border border-slate-300 px-2 py-1">
            {MEASURES.map((m) => (
              <option key={m} value={m}>
                {m || `${line.unit} (as bought)`}
              </option>
            ))}
          </select>
        </label>
        {needsCoverage && (
          <label className="text-xs text-slate-600">
            One {line.unit} covers ({measure})
            <input
              value={coverage}
              onChange={(e) => setCoverage(e.target.value)}
              inputMode="decimal"
              className="mt-1 block w-24 rounded border border-slate-300 px-2 py-1"
            />
          </label>
        )}
        <label className="text-xs text-slate-600">
          Waste %
          <input value={waste} onChange={(e) => setWaste(e.target.value)} inputMode="decimal" className="mt-1 block w-16 rounded border border-slate-300 px-2 py-1" />
        </label>
        {per && <span className="text-xs text-slate-600">= ${per}/{measure} with waste</span>}
        <button
          type="button"
          disabled={!name.trim() || (needsCoverage && !(Number(coverage) > 0))}
          onClick={save}
          className="rounded-md bg-slate-900 px-3 py-1 text-white disabled:opacity-50"
        >
          Add
        </button>
        <button type="button" onClick={onCancel} className="text-xs text-slate-600">
          cancel
        </button>
      </div>
    </div>
  );
}
