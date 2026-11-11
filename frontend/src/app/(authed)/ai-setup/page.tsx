"use client";

import { useCallback, useEffect, useState } from "react";

import { Box, Label, Tag } from "@/components/kit";
import { WorkspaceError } from "@/components/WorkspaceError";
import {
  getBudget,
  getFormingParams,
  listPractices,
  listSuppliers,
  savePractice,
  saveSupplier,
  setCap,
  setFormingParams,
  type Practice,
  type PrepBudget,
  type Supplier,
} from "@/lib/prep";
import { errorMessage } from "@/lib/workspaceApi";

/**
 * What the AI works with when it prepares documents (phase 9, 2026-10-13):
 * the monthly budget for the server engine (only Amir can raise it), whether
 * the Mac is available, BRIO's suppliers, and BRIO's practices: how BRIO does
 * things that no drawing says, which the drafting reads before every job.
 */
export default function AISetupPage() {
  const [b, setB] = useState<PrepBudget | null>(null);
  const [sup, setSup] = useState<Supplier[] | null>(null);
  const [pr, setPr] = useState<Practice[] | null>(null);
  const [loadErr, setLoadErr] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoadErr(null);
    getBudget().then(setB).catch(setLoadErr);
    listSuppliers().then(setSup).catch(setLoadErr);
    listPractices().then(setPr).catch(setLoadErr);
  }, []);
  useEffect(load, [load]);

  if (loadErr && !b) return <WorkspaceError error={loadErr} onRetry={load} />;

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <h1 className="m-0 text-2xl font-bold text-slate-900">AI setup</h1>
      {b && <Budget b={b} onChange={setB} />}
      <FormingSettings />
      <Suppliers rows={sup} onSaved={load} />
      <Practices rows={pr} onSaved={load} />
    </div>
  );
}

function Budget({ b, onChange }: { b: PrepBudget; onChange: (b: PrepBudget) => void }) {
  const [cap, setCapText] = useState(String(b.cap_usd));
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setErr(null);
    try {
      onChange(await setCap(Number(cap)));
    } catch (e) {
      setErr(errorMessage(e));
    }
  };
  const seen = b.mac_last_seen ? new Date(b.mac_last_seen).toLocaleString() : "never";
  return (
    <Box className="space-y-3 text-sm">
      <Label>This month</Label>
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <div className="text-xs text-slate-600">Preparing documents (server)</div>
          <div className="text-lg font-semibold">${b.prep_usd.toFixed(2)} <span className="text-sm font-normal text-slate-600">of ${b.cap_usd.toFixed(2)}</span></div>
        </div>
        <div>
          <div className="text-xs text-slate-600">Ask questions</div>
          <div className="text-lg font-semibold">${b.ask_usd.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-xs text-slate-600">Your Mac</div>
          <div className="flex items-center gap-2">
            <Tag tone={b.mac_available ? "ok" : "plain"}>{b.mac_available ? "On" : "Off"}</Tag>
            <span className="text-xs text-slate-600">last seen {seen}</span>
          </div>
        </div>
      </div>
      <p className="m-0 text-slate-600">
        Documents are prepared on your Mac first, at no AI cost. When the Mac is off, the server prepares them and
        stops when this month&rsquo;s budget is used. Ask questions cost a few cents each and are not stopped by the budget.
      </p>
      {b.can_change_cap ? (
        <div className="flex flex-wrap items-center gap-2">
          <span>Monthly budget $</span>
          <input value={cap} onChange={(e) => setCapText(e.target.value)} inputMode="decimal"
            className="w-24 rounded border border-slate-300 px-2 py-1" />
          <button type="button" onClick={save} className="rounded border border-slate-900 px-3 py-1">Save</button>
        </div>
      ) : (
        <p className="m-0 text-xs text-slate-500">Only Amir can change the budget.</p>
      )}
      {err && <p className="m-0 text-red-700">{err}</p>}
    </Box>
  );
}

const EMPTY_SUPPLIER = { name: "", trade: "", contact_name: "", email: "", phone: "", address: "", notes: "", active: true };

function Suppliers({ rows, onSaved }: { rows: Supplier[] | null; onSaved: () => void }) {
  const [edit, setEdit] = useState<(typeof EMPTY_SUPPLIER & { id?: string }) | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    if (!edit?.name.trim()) return;
    setErr(null);
    try {
      await saveSupplier({ ...edit, trade: edit.trade || null, contact_name: edit.contact_name || null,
        email: edit.email || null, phone: edit.phone || null, address: edit.address || null, notes: edit.notes || null });
      setEdit(null);
      onSaved();
    } catch (e) {
      setErr(errorMessage(e));
    }
  };
  const field = (k: keyof typeof EMPTY_SUPPLIER, label: string) => (
    <label className="block text-xs text-slate-600">{label}
      <input value={String(edit?.[k] ?? "")} onChange={(e) => setEdit(edit && { ...edit, [k]: e.target.value })}
        className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-sm text-slate-900" />
    </label>
  );
  return (
    <Box className="space-y-3 text-sm">
      <div className="flex items-center justify-between">
        <Label>Suppliers</Label>
        <button type="button" onClick={() => setEdit({ ...EMPTY_SUPPLIER })} className="text-blue-700">Add supplier</button>
      </div>
      <ul className="m-0 list-none divide-y divide-slate-100 p-0">
        {rows?.map((s) => (
          <li key={s.id} className="flex items-center justify-between gap-3 py-2">
            <span className={s.active ? "" : "text-slate-400 line-through"}>
              <b>{s.name}</b>{s.trade ? ` · ${s.trade}` : ""}
              {(s.contact_name || s.phone || s.email) && (
                <span className="block text-xs text-slate-600">{[s.contact_name, s.phone, s.email].filter(Boolean).join(" · ")}</span>
              )}
              {!s.phone && !s.email && <span className="block text-xs text-orange-800">No contact details yet</span>}
            </span>
            <button type="button" className="text-blue-700"
              onClick={() => setEdit({ id: s.id, name: s.name, trade: s.trade ?? "", contact_name: s.contact_name ?? "",
                email: s.email ?? "", phone: s.phone ?? "", address: s.address ?? "", notes: s.notes ?? "", active: s.active })}>
              Edit
            </button>
          </li>
        ))}
      </ul>
      {edit && (
        <div className="space-y-2 rounded-md border border-slate-200 p-3">
          <div className="grid gap-2 sm:grid-cols-2">
            {field("name", "Name")}{field("trade", "What they supply")}
            {field("contact_name", "Contact")}{field("phone", "Phone")}
            {field("email", "Email")}{field("address", "Address")}
          </div>
          {field("notes", "Notes")}
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} />
            In use
          </label>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setEdit(null)} className="px-3 py-1 text-slate-600">Cancel</button>
            <button type="button" onClick={save} className="rounded border border-slate-900 px-3 py-1">Save</button>
          </div>
        </div>
      )}
      {err && <p className="m-0 text-red-700">{err}</p>}
    </Box>
  );
}

function Practices({ rows, onSaved }: { rows: Practice[] | null; onSaved: () => void }) {
  const [edit, setEdit] = useState<{ id?: string; topic: string; rule: string; active: boolean } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    if (!edit || edit.rule.trim().length < 3) return;
    setErr(null);
    try {
      await savePractice({ ...edit, topic: edit.topic.trim() || "general" });
      setEdit(null);
      onSaved();
    } catch (e) {
      setErr(errorMessage(e));
    }
  };
  return (
    <Box className="space-y-3 text-sm">
      <div className="flex items-center justify-between">
        <Label>BRIO practices</Label>
        <button type="button" onClick={() => setEdit({ topic: "general", rule: "", active: true })} className="text-blue-700">
          Add practice
        </button>
      </div>
      <p className="m-0 text-slate-600">
        How BRIO does things that no drawing says. Read before every prepared document; a correction you ask it to
        remember lands here.
      </p>
      <ul className="m-0 list-none divide-y divide-slate-100 p-0">
        {rows?.map((c) => (
          <li key={c.id} className="flex items-start justify-between gap-3 py-2">
            <span className={c.active ? "" : "text-slate-400 line-through"}>
              <Tag>{c.topic}</Tag> {c.rule}
              {c.source && <span className="block text-xs text-slate-500">{c.source}</span>}
            </span>
            <button type="button" className="shrink-0 text-blue-700"
              onClick={() => setEdit({ id: c.id, topic: c.topic, rule: c.rule, active: c.active })}>Edit</button>
          </li>
        ))}
      </ul>
      {edit && (
        <div className="space-y-2 rounded-md border border-slate-200 p-3">
          <label className="block text-xs text-slate-600">Topic
            <input value={edit.topic} onChange={(e) => setEdit({ ...edit, topic: e.target.value })}
              className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-sm text-slate-900" />
          </label>
          <label className="block text-xs text-slate-600">Practice
            <textarea rows={3} value={edit.rule} onChange={(e) => setEdit({ ...edit, rule: e.target.value })}
              className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-sm text-slate-900" />
          </label>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} />
            In use
          </label>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setEdit(null)} className="px-3 py-1 text-slate-600">Cancel</button>
            <button type="button" onClick={save} className="rounded border border-slate-900 px-3 py-1">Save</button>
          </div>
        </div>
      )}
      {err && <p className="m-0 text-red-700">{err}</p>}
    </Box>
  );
}

const FORMING_LABELS: [string, string][] = [
  ["stake_spacing_ft", "Stake spacing, ft"], ["stakes_per_corner", "Stakes per corner"], ["board_length_ft", "Form board length, ft"],
  ["spare_pct", "Spare on lumber, %"], ["spreader_spacing_ft", "Spreader spacing, ft"], ["spreader_cut_in", "Spreader cut length, in"],
  ["spreader_stock_ft", "Spreader stock length, ft"], ["cleat_length_in", "Splice board length, in"],
  ["cleat_stock_ft", "Splice stock length, ft"], ["concrete_spare_pct", "Spare on concrete, %"],
];

/** The numbers the forming calculator counts with. Changing one changes every
 *  forming quantity from then on, on every project. */
function FormingSettings() {
  const [p, setP] = useState<Record<string, unknown> | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    getFormingParams().then(setP).catch((e) => setMsg(errorMessage(e)));
  }, []);
  if (!p) return null;
  const save = async () => {
    setMsg(null);
    try {
      const body = Object.fromEntries(FORMING_LABELS.map(([k]) => [k, Number(p[k])]));
      setP(await setFormingParams(body));
      setMsg("Saved. Forming quantities use these from now on.");
    } catch (e) {
      setMsg(errorMessage(e));
    }
  };
  const nails = (p.nails as { item: string; spec: string; qty: number; unit: string }[] | undefined) ?? [];
  return (
    <Box className="space-y-3 text-sm">
      <Label>Forming calculator</Label>
      <p className="m-0 text-slate-600">
        The numbers every footing-form order is counted with, from a project&rsquo;s confirmed foundation geometry.
        The same geometry always gives the same quantities.
      </p>
      <div className="grid gap-2 sm:grid-cols-3">
        {FORMING_LABELS.map(([k, l]) => (
          <label key={k} className="block text-xs text-slate-600">{l}
            <input value={String(p[k] ?? "")} inputMode="decimal" onChange={(e) => setP({ ...p, [k]: e.target.value })}
              className="mt-0.5 w-full rounded border border-slate-300 px-2 py-1 text-sm text-slate-900" />
          </label>
        ))}
      </div>
      <p className="m-0 text-xs text-slate-600">
        Nails on every footing order: {nails.map((n) => `${n.spec} ${n.qty} ${n.unit}`).join(", ")}
      </p>
      <div className="flex items-center justify-end gap-3">
        {msg && <span className="text-xs text-slate-600">{msg}</span>}
        <button type="button" onClick={save} className="rounded border border-slate-900 px-3 py-1">Save</button>
      </div>
    </Box>
  );
}
