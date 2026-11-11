"use client";

import { useSearchParams } from "next/navigation";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  type AssemblyBest,
  type AssemblyDetail,
  type LabourRate,
  type MaterialRate,
  type PriceResult,
  type Provenance,
  type SourceType,
  SOURCE_BADGE,
  SOURCE_LABEL,
  createAssembly,
  createEntry,
  createLabour,
  createMaterial,
  getAssembly,
  listAssemblies,
  listLabour,
  listMaterials,
  priceAssembly,
  setAssemblyStatus,
} from "@/lib/rates";

import { rateSourceWords } from "@/lib/rateSource";

import { AgeCell } from "./AgeCell";
import { CatalogueTab } from "./CatalogueTab";
import { ImportsTab } from "./ImportsTab";
import { ImportWizard } from "./ImportWizard";
import { applyFilters, isStale, LibraryLayout, NO_FILTERS, type Filters } from "./LibraryFilters";

type Tab = "assemblies" | "materials" | "labour" | "catalogue" | "imports";
const TABS: { key: Tab; label: string }[] = [
  { key: "assemblies", label: "Assemblies" },
  { key: "materials", label: "Materials" },
  { key: "labour", label: "Labour" },
  { key: "catalogue", label: "Supplier catalogue" },
  { key: "imports", label: "Imports" },
];

/** Plain words for a rate's source, as on the estimate page. */
function sourceWords(p: Provenance | null | undefined): string {
  return p ? rateSourceWords(p.source_type, p.confidence, p.source_ref) : "no rate";
}

const PROJECT_TYPES = ["any", "residential", "commercial"] as const;
const SOURCE_TYPES: SourceType[] = [
  "subcontract_derived",
  "published",
  "composed",
  "market",
];

function SourceBadge({ p }: { p: Provenance | null }) {
  if (!p)
    return (
      <span className="inline-block rounded border border-red-200 bg-red-50 px-2 py-0.5 text-xs text-red-700">
        no rate
      </span>
    );
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 text-xs ${SOURCE_BADGE[p.source_type]}`}
      title={p.source_ref ?? ""}
    >
      {SOURCE_LABEL[p.source_type]} · {p.confidence}
    </span>
  );
}

const money = (n: number | null | undefined) =>
  n == null ? "—" : `$${n.toLocaleString("en-CA", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function RatesPage() {
  const [projectType, setProjectType] = useState<string>("any");
  // ?tab=catalogue etc. opens that tab (the Libraries page links there).
  const askedTab = useSearchParams().get("tab") as Tab | null;
  const [tab, setTab] = useState<Tab>(askedTab && ["assemblies", "materials", "labour", "catalogue", "imports"].includes(askedTab) ? askedTab : "assemblies");
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [importing, setImporting] = useState(false);
  // Bumped after an import, undo or add, so every tab re-reads.
  const [refreshKey, setRefreshKey] = useState(0);
  const bump = () => setRefreshKey((k) => k + 1);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1
            className="text-xl font-semibold"
            style={{ color: "var(--color-brio-accent)" }}
          >
            Rate Library
          </h1>
          <p className="text-sm text-slate-500">
            Installed unit rates for deterministic estimate pricing. Source
            trust: subcontract-derived → published → composed → market.
          </p>
        </div>
        <label className="text-sm text-slate-600">
          Price for{" "}
          <select
            value={projectType}
            onChange={(e) => setProjectType(e.target.value)}
            className="ml-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
          >
            {PROJECT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <PriceCheck projectType={projectType} onError={setError} />

      <div className="flex flex-wrap items-end justify-between gap-2 border-b border-slate-200">
        <div className="flex gap-2">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => {
                setTab(t.key);
                setFilters(NO_FILTERS);
              }}
              className={`px-3 py-2 text-sm ${
                tab === t.key
                  ? "border-b-2 border-slate-800 font-medium text-slate-900"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <Button variant="secondary" onClick={() => setImporting(true)} className="mb-1">
          Import price sheet
        </Button>
      </div>

      {tab === "assemblies" && (
        <AssembliesTab key={refreshKey} projectType={projectType} onError={setError} filters={filters} setFilters={setFilters} />
      )}
      {tab === "materials" && <MaterialsTab key={refreshKey} onError={setError} filters={filters} setFilters={setFilters} />}
      {tab === "labour" && <LabourTab key={refreshKey} onError={setError} filters={filters} setFilters={setFilters} />}
      {tab === "catalogue" && <CatalogueTab key={refreshKey} onError={setError} onAdded={bump} />}
      {tab === "imports" && <ImportsTab onError={setError} refreshKey={refreshKey} onChanged={bump} />}
      {importing && (
        <ImportWizard
          onClose={() => setImporting(false)}
          onDone={() => {
            setImporting(false);
            setTab("imports");
            bump();
          }}
        />
      )}
    </div>
  );
}

// ── Price check ─────────────────────────────────────────────────────

function PriceCheck({
  projectType,
  onError,
}: {
  projectType: string;
  onError: (e: string) => void;
}) {
  const [assemblies, setAssemblies] = useState<AssemblyBest[]>([]);
  const [code, setCode] = useState("");
  const [qty, setQty] = useState("1000");
  const [result, setResult] = useState<PriceResult | null>(null);

  useEffect(() => {
    listAssemblies("any")
      .then((a) => {
        setAssemblies(a);
        if (a.length && !code) setCode(a[0].code);
      })
      .catch((e) => onError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = async () => {
    try {
      setResult(
        await priceAssembly({
          assembly_code: code,
          quantity: Number(qty) || 0,
          project_type: projectType,
        }),
      );
    } catch (e) {
      onError(String(e));
    }
  };

  return (
    <Card className="p-4">
      <div className="mb-2 text-sm font-medium text-slate-700">Price check</div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-slate-500">
          Assembly
          <select
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className="mt-1 block w-72 rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            {assemblies.map((a) => (
              <option key={a.code} value={a.code}>
                {a.code} — {a.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-500">
          Quantity
          <Input
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            className="mt-1 w-32"
            inputMode="decimal"
          />
        </label>
        <Button onClick={run}>Price</Button>
        {result &&
          (result.priced ? (
            <div className="text-sm">
              <span className="text-lg font-semibold">
                {money(result.amount)}
              </span>{" "}
              <span className="text-slate-500">
                = {result.quantity} × {money(result.unit_rate)}/{result.unit}
              </span>
              <div className="mt-1">
                <SourceBadge p={result.provenance ?? null} />
                {result.provenance?.source_ref && (
                  <span className="ml-2 text-xs text-slate-400">
                    {result.provenance.source_ref}
                  </span>
                )}
              </div>
            </div>
          ) : (
            <div className="text-sm text-red-600">
              No rate for this assembly / project type — estimate would fall back
              to LLM pricing.
            </div>
          ))}
      </div>
    </Card>
  );
}

// ── Assemblies ──────────────────────────────────────────────────────

function AssembliesTab({
  projectType,
  onError,
  filters,
  setFilters,
}: {
  projectType: string;
  onError: (e: string) => void;
  filters: Filters;
  setFilters: (f: Filters) => void;
}) {
  const [rows, setRows] = useState<AssemblyBest[] | null>(null);
  // The review tray: drafts and held items are listed separately from live rates.
  const [status, setStatus] = useState<"active" | "draft" | "held" | "retired">("active");
  const [tray, setTray] = useState<{ draft: number; held: number; retired: number }>({ draft: 0, held: 0, retired: 0 });
  // An estimate line's "open rate" link lands here with ?code=FL-LVP.
  const [open, setOpen] = useState<string | null>(() =>
    typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("code"),
  );
  const [showNew, setShowNew] = useState(false);

  const refresh = useCallback(() => {
    listAssemblies(projectType, status)
      .then(setRows)
      .catch((e) => onError(String(e)));
    Promise.all((["draft", "held", "retired"] as const).map((st) => listAssemblies(projectType, st)))
      .then(([d, h, r]) => setTray({ draft: d.length, held: h.length, retired: r.length }))
      .catch((e) => onError(String(e)));
  }, [projectType, status, onError]);

  useEffect(refresh, [refresh]);

  const get = (a: AssemblyBest) => ({
    division: a.division,
    trade_key: a.trade_key,
    trade_label: a.trade_label,
    unit: a.unit,
    source: a.provenance?.source_type,
    stale: isStale(a.provenance?.as_of_date, a.drift),
    text: `${a.code} ${a.name}`,
  });
  const shown = rows ? applyFilters(rows, filters, get) : null;
  const trayButton = (st: typeof status, label: string, n: number | null) => (
    <button
      type="button"
      onClick={() => setStatus(st)}
      className={`block w-full rounded px-2 py-1 text-left ${status === st ? "bg-slate-900 text-white" : "hover:bg-slate-100"}`}
    >
      {label} <span className="float-right tabular-nums">{n ?? ""}</span>
    </button>
  );

  return (
    <LibraryLayout
      rows={rows ?? []}
      get={get}
      filters={filters}
      setFilters={setFilters}
      units={[...new Set((rows ?? []).map((r) => r.unit))].sort()}
      sources={SOURCE_TYPES.map((t) => ({ value: t, label: SOURCE_LABEL[t] }))}
      tray={
        <div>
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Review tray</div>
          {trayButton("active", "Live rates", status === "active" ? rows?.length ?? 0 : null)}
          {trayButton("draft", "Drafts", tray.draft)}
          {trayButton("held", "Held", tray.held)}
          {tray.retired > 0 && trayButton("retired", "Retired", tray.retired)}
        </div>
      }
    >
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-600">
          {status === "active" ? "Live rates: these price estimates." : status === "draft" ? "Drafts: waiting for review; they never price." : status === "held" ? "Held until a number is confirmed; they never price." : "Retired: kept for history; they never price."}
        </span>
        <Button variant="secondary" onClick={() => setShowNew((s) => !s)}>
          {showNew ? "Cancel" : "+ New assembly"}
        </Button>
      </div>
      {showNew && (
        <NewAssemblyForm
          onDone={() => {
            setShowNew(false);
            refresh();
          }}
          onError={onError}
        />
      )}
      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Code</th>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2 text-right">Rate</th>
              <th className="px-3 py-2">Source</th>
              <th className="px-3 py-2">Age</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {shown?.map((a) => (
              <RateRow
                key={a.code}
                a={a}
                projectType={projectType}
                open={open === a.code}
                onToggle={() => setOpen(open === a.code ? null : a.code)}
                onError={onError}
                onChanged={refresh}
              />
            ))}
            {shown && shown.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-slate-400">
                  Nothing matches.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
    </LibraryLayout>
  );
}

function RateRow({
  a,
  projectType,
  open,
  onToggle,
  onError,
  onChanged,
}: {
  a: AssemblyBest;
  projectType: string;
  open: boolean;
  onToggle: () => void;
  onError: (e: string) => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<AssemblyDetail | null>(null);
  const [adding, setAdding] = useState(false);
  const changeStatus = async (to: "active" | "retired") => {
    try {
      await setAssemblyStatus(a.code, to);
      onChanged();
    } catch (e) {
      onError(String(e));
    }
  };

  useEffect(() => {
    if (open && !detail)
      getAssembly(a.code, projectType)
        .then(setDetail)
        .catch((e) => onError(String(e)));
  }, [open, detail, a.code, projectType, onError]);

  return (
    <>
      <tr className="border-t border-slate-100 hover:bg-slate-50">
        <td className="px-3 py-2 font-mono text-xs">{a.code}</td>
        <td className="px-3 py-2">{a.name}</td>
        <td className="px-3 py-2 text-right whitespace-nowrap">
          {a.unit_rate == null ? (
            <span className="text-red-600">—</span>
          ) : (
            <>
              {money(a.unit_rate)}
              <span className="text-slate-400">/{a.unit}</span>
            </>
          )}
        </td>
        <td className="px-3 py-2 text-xs">{sourceWords(a.provenance)}</td>
        <td className="px-3 py-2">
          <AgeCell asOf={a.provenance?.as_of_date} drift={a.drift} />
        </td>
        <td className="px-3 py-2 text-right">
          <button
            onClick={onToggle}
            className="text-xs text-slate-500 hover:text-slate-800"
          >
            {open ? "hide" : "history"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="bg-slate-50/60">
          <td colSpan={6} className="px-3 py-3">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-xs text-slate-500">
                {a.trade_label ?? a.trade} · {a.project_type}
                {detail?.scope_notes && ` · ${detail.scope_notes}`}
                {detail?.used_on_estimates != null && ` · used on ${detail.used_on_estimates} estimate${detail.used_on_estimates === 1 ? "" : "s"}`}
              </div>
              <div className="flex gap-2">
                {(a.status === "draft" || a.status === "held" || a.status === "retired") && (
                  <Button variant="secondary" className="!px-2 !py-1 text-xs" onClick={() => changeStatus("active")}>
                    Release
                  </Button>
                )}
                {a.status === "active" && (
                  <Button variant="secondary" className="!px-2 !py-1 text-xs" onClick={() => changeStatus("retired")}>
                    Retire
                  </Button>
                )}
              <Button
                variant="secondary"
                onClick={() => setAdding((s) => !s)}
                className="!px-2 !py-1 text-xs"
              >
                {adding ? "Cancel" : "+ Add rate"}
              </Button>
              </div>
            </div>
            {adding && (
              <NewEntryForm
                assemblyCode={a.code}
                unit={a.unit}
                onDone={() => {
                  setAdding(false);
                  setDetail(null);
                  onChanged();
                }}
                onError={onError}
              />
            )}
            <table className="w-full text-xs">
              <thead className="text-left text-slate-400">
                <tr>
                  <th className="py-1">Rate</th>
                  <th className="py-1">Source</th>
                  <th className="py-1">Project</th>
                  <th className="py-1">As of</th>
                  <th className="py-1">Ref</th>
                  <th className="py-1">Active</th>
                </tr>
              </thead>
              <tbody>
                {detail?.entries.map((e) => (
                  <tr key={e.id} className="border-t border-slate-200">
                    <td className="py-1">
                      {money(e.unit_rate)}/{e.unit}
                    </td>
                    <td className="py-1">
                      {SOURCE_LABEL[e.source_type]} · {e.confidence}
                    </td>
                    <td className="py-1">{e.project_type}</td>
                    <td className="py-1">{e.as_of_date ?? "—"}</td>
                    <td className="py-1 text-slate-400">{e.source_ref ?? "—"}</td>
                    <td className="py-1">
                      {e.is_active ? (
                        e.id === detail.best_entry_id ? (
                          <span className="text-emerald-700">✓ best</span>
                        ) : (
                          <span className="text-slate-500">active</span>
                        )
                      ) : (
                        <span className="text-slate-300">superseded</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Forms ───────────────────────────────────────────────────────────

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="text-xs text-slate-500">
      {label}
      <div className="mt-1">{children}</div>
    </label>
  );
}

function NewAssemblyForm({
  onDone,
  onError,
}: {
  onDone: () => void;
  onError: (e: string) => void;
}) {
  const [f, setF] = useState({
    code: "",
    name: "",
    trade: "",
    unit: "sqft",
    category: "",
    project_type: "any",
    scope_notes: "",
  });
  const set = (k: string) => (e: { target: { value: string } }) =>
    setF((p) => ({ ...p, [k]: e.target.value }));

  const submit = async () => {
    try {
      await createAssembly({
        code: f.code,
        name: f.name,
        trade: f.trade,
        unit: f.unit,
        category: f.category || null,
        project_type: f.project_type,
        scope_notes: f.scope_notes || null,
      });
      onDone();
    } catch (e) {
      onError(String(e));
    }
  };

  return (
    <Card className="grid grid-cols-2 gap-3 p-4 md:grid-cols-4">
      <Field label="Code">
        <Input value={f.code} onChange={set("code")} placeholder="FRAME-…" />
      </Field>
      <Field label="Name">
        <Input value={f.name} onChange={set("name")} />
      </Field>
      <Field label="Trade">
        <Input value={f.trade} onChange={set("trade")} placeholder="framing" />
      </Field>
      <Field label="Unit">
        <Input value={f.unit} onChange={set("unit")} placeholder="sqft" />
      </Field>
      <Field label="Category">
        <Input value={f.category} onChange={set("category")} />
      </Field>
      <Field label="Project type">
        <select
          value={f.project_type}
          onChange={set("project_type")}
          className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
        >
          {PROJECT_TYPES.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </Field>
      <Field label="Scope notes">
        <Input value={f.scope_notes} onChange={set("scope_notes")} />
      </Field>
      <div className="flex items-end">
        <Button onClick={submit}>Create</Button>
      </div>
    </Card>
  );
}

function NewEntryForm({
  assemblyCode,
  unit,
  onDone,
  onError,
}: {
  assemblyCode: string;
  unit: string;
  onDone: () => void;
  onError: (e: string) => void;
}) {
  const [f, setF] = useState({
    unit_rate: "",
    source_type: "subcontract_derived" as SourceType,
    confidence: "measured",
    project_type: "any",
    source_ref: "",
    as_of_date: "",
  });
  const set = (k: string) => (e: { target: { value: string } }) =>
    setF((p) => ({ ...p, [k]: e.target.value }));

  const submit = async () => {
    try {
      await createEntry({
        assembly_code: assemblyCode,
        unit_rate: Number(f.unit_rate),
        source_type: f.source_type,
        confidence: f.confidence,
        project_type: f.project_type,
        source_ref: f.source_ref || null,
        as_of_date: f.as_of_date || null,
      });
      onDone();
    } catch (e) {
      onError(String(e));
    }
  };

  return (
    <div className="mb-3 grid grid-cols-2 gap-2 rounded-md border border-slate-200 bg-white p-3 md:grid-cols-6">
      <Field label={`Rate /${unit}`}>
        <Input value={f.unit_rate} onChange={set("unit_rate")} inputMode="decimal" />
      </Field>
      <Field label="Source">
        <select
          value={f.source_type}
          onChange={set("source_type")}
          className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
        >
          {SOURCE_TYPES.map((s) => (
            <option key={s} value={s}>
              {SOURCE_LABEL[s]}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Confidence">
        <Input value={f.confidence} onChange={set("confidence")} />
      </Field>
      <Field label="Project">
        <select
          value={f.project_type}
          onChange={set("project_type")}
          className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
        >
          {PROJECT_TYPES.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </Field>
      <Field label="As of">
        <Input value={f.as_of_date} onChange={set("as_of_date")} placeholder="2026-07-13" />
      </Field>
      <Field label="Source ref">
        <Input value={f.source_ref} onChange={set("source_ref")} />
      </Field>
      <div className="md:col-span-6">
        <Button onClick={submit} className="!px-3 !py-1.5 text-xs">
          Add rate (supersedes prior active same source/type)
        </Button>
      </div>
    </div>
  );
}

// ── Materials ───────────────────────────────────────────────────────

function MaterialsTab({
  onError,
  filters,
  setFilters,
}: {
  onError: (e: string) => void;
  filters: Filters;
  setFilters: (f: Filters) => void;
}) {
  const [rows, setRows] = useState<MaterialRate[] | null>(null);
  const [show, setShow] = useState(false);
  const refresh = useCallback(() => {
    listMaterials()
      .then(setRows)
      .catch((e) => onError(String(e)));
  }, [onError]);
  useEffect(refresh, [refresh]);
  const getM = (m: MaterialRate) => ({
    division: m.division,
    trade_key: m.trade_key,
    trade_label: m.trade_label,
    unit: m.unit,
    source: m.source,
    stale: isStale(m.as_of_date, m.drift),
    text: `${m.name} ${m.sku ?? ""} ${m.category ?? ""}`,
  });

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button variant="secondary" onClick={() => setShow((s) => !s)}>
          {show ? "Cancel" : "+ Add material"}
        </Button>
      </div>
      {show && (
        <NewMaterialForm
          onDone={() => {
            setShow(false);
            refresh();
          }}
          onError={onError}
        />
      )}
      <LibraryLayout
        rows={rows ?? []}
        get={getM}
        filters={filters}
        setFilters={setFilters}
        units={[...new Set((rows ?? []).map((r) => r.unit))].sort()}
        sources={[...new Set((rows ?? []).map((r) => r.source))].map((v) => ({ value: v, label: v }))}
      >
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2 text-right">Bought as</th>
              <th className="px-3 py-2 text-right">Covers</th>
              <th className="px-3 py-2 text-right">Per takeoff unit</th>
              <th className="px-3 py-2">Source</th>
              <th className="px-3 py-2">Age</th>
            </tr>
          </thead>
          <tbody>
            {(rows ? applyFilters(rows, filters, getM) : []).map((m) => (
              <tr key={m.id} className="border-t border-slate-100">
                <td className="px-3 py-2">
                  {m.name}
                  <div className="text-[11px] text-slate-500">
                    {m.trade_label ?? m.trade ?? "—"}
                    {m.category ? ` › ${m.category}` : ""}
                    {m.sku ? ` · SKU ${m.sku}` : ""}
                  </div>
                </td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  {money(m.unit_cost)}
                  <span className="text-slate-400">/{m.unit}</span>
                  {/* Supply-only band for choosing a band before a client sees it; muted. */}
                  {m.band && (
                    <div className="text-[11px] text-slate-400" title="Supply-only cost band; the cost shown is the mid.">
                      {m.band.low} / {m.band.mid} / {m.band.high}
                    </div>
                  )}
                </td>
                <td className="px-3 py-2 text-right text-xs text-slate-600">
                  {m.coverage && m.measure_unit && m.measure_unit !== m.unit ? `${m.coverage} ${m.measure_unit}` : "—"}
                </td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  {m.cost_per_measure ? (
                    <>
                      {money(m.cost_per_measure.usd)}
                      <span className="text-slate-400">/{m.cost_per_measure.unit}</span>
                      {m.waste_pct ? <div className="text-[11px] text-slate-400">incl. {m.waste_pct}% waste</div> : null}
                    </>
                  ) : (
                    <span className="text-xs text-orange-800" title="Bought by a pack with no coverage set">
                      needs coverage
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-xs">
                  {m.source === "own" ? "your rate" : m.source}
                  {m.supplier ? ` · ${m.supplier}` : ""}
                </td>
                <td className="px-3 py-2">
                  <AgeCell asOf={m.as_of_date} drift={m.drift} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      </LibraryLayout>
    </div>
  );
}

function NewMaterialForm({
  onDone,
  onError,
}: {
  onDone: () => void;
  onError: (e: string) => void;
}) {
  const [f, setF] = useState({
    name: "",
    trade: "",
    unit: "",
    unit_cost: "",
    source: "supplier",
    supplier: "",
    waste_pct: "",
  });
  const set = (k: string) => (e: { target: { value: string } }) =>
    setF((p) => ({ ...p, [k]: e.target.value }));
  const submit = async () => {
    try {
      await createMaterial({
        name: f.name,
        trade: f.trade || null,
        unit: f.unit,
        unit_cost: Number(f.unit_cost),
        source: f.source,
        supplier: f.supplier || null,
        waste_pct: f.waste_pct ? Number(f.waste_pct) : null,
      });
      onDone();
    } catch (e) {
      onError(String(e));
    }
  };
  return (
    <Card className="grid grid-cols-2 gap-3 p-4 md:grid-cols-4">
      <Field label="Name">
        <Input value={f.name} onChange={set("name")} />
      </Field>
      <Field label="Trade">
        <Input value={f.trade} onChange={set("trade")} />
      </Field>
      <Field label="Unit">
        <Input value={f.unit} onChange={set("unit")} placeholder="sheet / bf / ea" />
      </Field>
      <Field label="Unit cost">
        <Input value={f.unit_cost} onChange={set("unit_cost")} inputMode="decimal" />
      </Field>
      <Field label="Source">
        <select
          value={f.source}
          onChange={set("source")}
          className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
        >
          <option value="supplier">supplier (ours)</option>
          <option value="market">market (fallback)</option>
        </select>
      </Field>
      <Field label="Supplier">
        <Input value={f.supplier} onChange={set("supplier")} />
      </Field>
      <Field label="Waste %">
        <Input value={f.waste_pct} onChange={set("waste_pct")} inputMode="decimal" />
      </Field>
      <div className="flex items-end">
        <Button onClick={submit}>Add</Button>
      </div>
    </Card>
  );
}

// ── Labour ──────────────────────────────────────────────────────────

function LabourTab({
  onError,
  filters,
  setFilters,
}: {
  onError: (e: string) => void;
  filters: Filters;
  setFilters: (f: Filters) => void;
}) {
  const [rows, setRows] = useState<LabourRate[] | null>(null);
  const [show, setShow] = useState(false);
  const refresh = useCallback(() => {
    listLabour()
      .then(setRows)
      .catch((e) => onError(String(e)));
  }, [onError]);
  useEffect(refresh, [refresh]);
  const getL = (r: LabourRate) => ({
    division: r.division,
    trade_key: r.trade_key,
    trade_label: r.trade_label,
    unit: "hr",
    source: r.source,
    stale: isStale(r.as_of_date),
    text: `${r.trade} ${r.source_ref ?? ""}`,
  });

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button variant="secondary" onClick={() => setShow((s) => !s)}>
          {show ? "Cancel" : "+ Add labour rate"}
        </Button>
      </div>
      {show && (
        <NewLabourForm
          onDone={() => {
            setShow(false);
            refresh();
          }}
          onError={onError}
        />
      )}
      <LibraryLayout
        rows={rows ?? []}
        get={getL}
        filters={filters}
        setFilters={setFilters}
        units={[]}
        sources={[...new Set((rows ?? []).map((r) => r.source))].map((v) => ({ value: v, label: v }))}
      >
      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Trade</th>
              <th className="px-3 py-2">Source</th>
              <th className="px-3 py-2 text-right">Base</th>
              <th className="px-3 py-2 text-right">Disc %</th>
              <th className="px-3 py-2 text-right">Effective</th>
              <th className="px-3 py-2 text-right">Billable l/m/h</th>
              <th className="px-3 py-2">Age</th>
            </tr>
          </thead>
          <tbody>
            {(rows ? applyFilters(rows, filters, getL) : []).map((r) => (
              <tr key={r.id} className="border-t border-slate-100">
                <td className="px-3 py-2">{r.trade}</td>
                <td className="px-3 py-2">
                  <span
                    className={`rounded border px-2 py-0.5 text-xs ${
                      r.source === "own"
                        ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                        : "border-slate-300 bg-slate-100 text-slate-600"
                    }`}
                  >
                    {r.source}
                  </span>
                </td>
                <td className="px-3 py-2 text-right">{money(r.base_rate)}</td>
                <td className="px-3 py-2 text-right">{r.discount_pct}</td>
                <td className="px-3 py-2 text-right font-medium">
                  {money(r.effective_rate)}
                </td>
                {/* Comparison reference, not a cost — muted so it never reads as
                    the rate the estimator uses. */}
                <td
                  className="px-3 py-2 text-right text-xs text-slate-400"
                  title="BRIO billable $/hr incl. overhead — for choosing a band. Never priced directly."
                >
                  {r.billable_band
                    ? `${r.billable_band.low} / ${r.billable_band.mid} / ${r.billable_band.high}`
                    : "\u2014"}
                </td>
                <td className="px-3 py-2">
                  <AgeCell asOf={r.as_of_date} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      </LibraryLayout>
    </div>
  );
}

function NewLabourForm({
  onDone,
  onError,
}: {
  onDone: () => void;
  onError: (e: string) => void;
}) {
  const [f, setF] = useState({
    trade: "",
    source: "own",
    base_rate: "",
    project_type: "any",
    discount_pct: "",
  });
  const set = (k: string) => (e: { target: { value: string } }) =>
    setF((p) => ({ ...p, [k]: e.target.value }));

  const preview = useMemo(() => {
    const base = Number(f.base_rate);
    if (!base) return null;
    // No implied discount. Published wage data is stored as posted; a discount
    // is only applied when one is entered deliberately.
    const disc = f.discount_pct ? Number(f.discount_pct) : 0;
    return Math.round(base * (1 - disc / 100) * 100) / 100;
  }, [f.base_rate, f.source, f.discount_pct]);

  const submit = async () => {
    try {
      await createLabour({
        trade: f.trade,
        source: f.source,
        base_rate: Number(f.base_rate),
        project_type: f.project_type,
        discount_pct: f.discount_pct ? Number(f.discount_pct) : null,
      });
      onDone();
    } catch (e) {
      onError(String(e));
    }
  };

  return (
    <Card className="grid grid-cols-2 gap-3 p-4 md:grid-cols-5">
      <Field label="Trade">
        <Input value={f.trade} onChange={set("trade")} placeholder="framer" />
      </Field>
      <Field label="Source">
        <select
          value={f.source}
          onChange={set("source")}
          className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
        >
          <option value="own">own (ours)</option>
          <option value="association">association</option>
          <option value="workbc">workbc</option>
          <option value="market">market</option>
        </select>
      </Field>
      <Field label="Base $/hr">
        <Input value={f.base_rate} onChange={set("base_rate")} inputMode="decimal" />
      </Field>
      <Field label={`Discount % ${f.source === "own" ? "(n/a)" : "(def 20)"}`}>
        <Input
          value={f.discount_pct}
          onChange={set("discount_pct")}
          inputMode="decimal"
          disabled={f.source === "own"}
        />
      </Field>
      <Field label="Project">
        <select
          value={f.project_type}
          onChange={set("project_type")}
          className="w-full rounded-md border border-slate-300 px-2 py-2 text-sm"
        >
          {PROJECT_TYPES.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </Field>
      <div className="flex items-end gap-3 md:col-span-5">
        <Button onClick={submit}>Add</Button>
        {preview != null && (
          <span className="text-sm text-slate-500">
            Effective: <span className="font-medium">{money(preview)}/hr</span>
          </span>
        )}
      </div>
    </Card>
  );
}
