"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { CostApproval } from "@/components/CostApproval";
import { GeometryCard } from "@/components/GeometryCard";
import { Box, Label, Tag } from "@/components/kit";
import { WorkspaceError } from "@/components/WorkspaceError";
import { getDownloadUrl } from "@/lib/documents";
import {
  answerPrep,
  cancelPrep,
  editPrepDoc,
  estimateStep,
  finalizePrep,
  getPrep,
  previewUrl,
  retryPrep,
  revisePrep,
  STATUS_LABEL,
  type CostEstimate,
  type PreparedDoc,
  type PrepPayload,
  type PrepRequest,
} from "@/lib/prep";
import { errorMessage } from "@/lib/workspaceApi";

/**
 * One prepared-document request: what it is doing now, the questions it needs
 * answered, then the draft to check. Every line shows how its number was
 * worked out and where from; that working is for the office and is never
 * printed. Correct by editing a cell, or by telling it what is wrong (and
 * ticking "remember" to make it a BRIO practice for every later job).
 */

const BUSY = new Set(["queued", "running"]);

export default function PrepRequestPage({ params }: { params: Promise<{ id: string; rid: string }> }) {
  const { id: projectId, rid } = use(params);
  const [req, setReq] = useState<PrepRequest | null>(null);
  const [loadErr, setLoadErr] = useState<unknown>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getPrep(projectId, rid).then((r) => { setReq(r); setLoadErr(null); }).catch(setLoadErr);
  }, [projectId, rid]);
  useEffect(load, [load]);
  useEffect(() => {
    if (!req || !BUSY.has(req.status)) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [req, load]);

  const act = async (fn: () => Promise<PrepRequest>) => {
    setBusy(true);
    setErr(null);
    try {
      setReq(await fn());
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  if (loadErr && !req) return <WorkspaceError error={loadErr} onRetry={load} />;
  if (!req) return <p className="text-sm text-slate-500">Loading…</p>;

  const plan = req.plan ?? {};
  const asking = req.status === "waiting_user" && req.stage === "plan";
  const reviewing = req.status === "waiting_user" && req.stage !== "plan";
  const last = req.progress[req.progress.length - 1];

  return (
    <div className="space-y-4">
      <div className="text-[13px]">
        <Link href={`/projects/${projectId}/prepare`} className="text-blue-700">Prepare</Link>
        <span className="text-slate-500"> / request</span>
      </div>

      <Box className="space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <p className="m-0 min-w-0 flex-1 text-[15px] font-medium text-slate-900">{req.request}</p>
          <span className="flex items-center gap-2 text-xs text-slate-600">
            {req.engine && <span>{req.engine === "mac" ? "Your Mac (no AI cost)" : "Server"}</span>}
            {req.cost_usd > 0 && <span>${req.cost_usd.toFixed(2)}</span>}
            <Tag tone={req.status === "waiting_user" ? "warn" : req.status === "finalized" ? "ok" : "plain"}>
              {STATUS_LABEL[req.status]}
            </Tag>
          </span>
        </div>
        {BUSY.has(req.status) && (
          <p className="m-0 text-sm text-slate-700">
            {last?.msg ?? "Starting…"}
            {req.status === "queued" && !req.mac_available && " · the Mac is off, so the server will take it"}
          </p>
        )}
        {req.status === "failed" && (
          <div className="flex flex-wrap items-center gap-3 text-sm text-red-700">
            <span>{req.error}</span>
          </div>
        )}
        {req.status === "failed" && (
          <CostApproval load={() => estimateStep(projectId, rid, req.stage)} action="Retry" busy={busy}
            onApprove={(usd) => act(() => retryPrep(projectId, rid, usd))} />
        )}
        {req.progress.length > 0 && (
          <details className="text-xs text-slate-600">
            <summary className="cursor-pointer">Activity ({req.progress.length})</summary>
            <ul className="m-0 mt-1 list-none space-y-0.5 p-0">
              {req.progress.map((p, i) => (
                <li key={i}>{new Date(p.at).toLocaleTimeString()} · {p.msg}</li>
              ))}
            </ul>
          </details>
        )}
        {err && <p className="m-0 text-sm text-red-700">{err}</p>}
      </Box>

      {plan.summary && (
        <Box className="space-y-2 text-sm">
          <Label>Plan</Label>
          <p className="m-0">{plan.summary}</p>
          {plan.sources && plan.sources.length > 0 && (
            <ul className="m-0 list-disc pl-5 text-slate-700">
              {plan.sources.map((s, i) => <li key={i}>{s.label}{s.why ? `: ${s.why}` : ""}</li>)}
            </ul>
          )}
        </Box>
      )}

      {req.geometry && (req.plan?.geometry_to_confirm || req.geometry.status === "confirmed") && (
        <GeometryCard key={`${req.geometry.version}-${req.geometry.status}`} projectId={projectId} state={req.geometry}
          editable={asking || reviewing} onConfirmed={load} />
      )}

      {asking && (
        <Questions req={req} busy={busy}
          needsGeometry={!!req.plan?.geometry_to_confirm && req.geometry?.confirmed_version == null}
          estimate={() => estimateStep(projectId, rid, "draft")}
          onSubmit={(a, usd) => act(() => answerPrep(projectId, rid, a, usd))} />
      )}

      {(plan.conflicts?.length || plan.assumptions?.length) && req.stage !== "plan" ? (
        <Box className="space-y-2 border-orange-300 bg-orange-50 text-sm">
          {plan.conflicts && plan.conflicts.length > 0 && (
            <>
              <Label className="text-orange-900">Decide before using</Label>
              <ul className="m-0 list-disc pl-5">{plan.conflicts.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </>
          )}
          {plan.assumptions && plan.assumptions.length > 0 && (
            <>
              <Label className="text-orange-900">Assumed: check these</Label>
              <ul className="m-0 list-disc pl-5">{plan.assumptions.map((c, i) => <li key={i}>{c}</li>)}</ul>
            </>
          )}
        </Box>
      ) : null}

      {req.documents.map((d) => (
        <DocEditor key={`${d.id}-${d.version}`} doc={d} editable={reviewing} projectId={projectId} rid={rid}
          onSaved={setReq} />
      ))}

      {reviewing && (
        <Correction busy={busy} estimate={() => estimateStep(projectId, rid, "revise")}
          onSend={(c, remember, usd) => act(() => revisePrep(projectId, rid, c, remember, usd))}
          onFinalize={() => act(() => finalizePrep(projectId, rid))}
          onCancel={() => act(() => cancelPrep(projectId, rid))} />
      )}
    </div>
  );
}

function Questions({ req, busy, needsGeometry, estimate, onSubmit }: {
  req: PrepRequest; busy: boolean; needsGeometry: boolean; estimate: () => Promise<CostEstimate>;
  onSubmit: (a: Record<string, string>, usd: number) => void;
}) {
  const [load] = useState(() => estimate);
  const qs = req.plan?.questions ?? [];
  const [a, setA] = useState<Record<string, string>>(
    Object.fromEntries(qs.map((q) => [q.id, q.suggested ?? ""])),
  );
  return (
    <Box className="space-y-3 text-sm">
      <Label>Before drafting</Label>
      {qs.map((q) => (
        <label key={q.id} className="block space-y-1">
          <span className="font-medium text-slate-900">{q.question}</span>
          {q.why && <span className="block text-xs text-slate-600">{q.why}</span>}
          <input value={a[q.id] ?? ""} onChange={(e) => setA({ ...a, [q.id]: e.target.value })}
            className="w-full rounded-md border border-slate-300 px-3 py-1.5" />
        </label>
      ))}
      {needsGeometry && (
        <p className="m-0 text-orange-800">Confirm the foundation geometry above first: every quantity is counted from it.</p>
      )}
      <CostApproval load={load} action="Draft it" busy={busy} disabled={needsGeometry} onApprove={(usd) => onSubmit(a, usd)} />
    </Box>
  );
}

function DocEditor({ doc, editable, projectId, rid, onSaved }: {
  doc: PreparedDoc; editable: boolean; projectId: string; rid: string; onSaved: (r: PrepRequest) => void;
}) {
  const [title, setTitle] = useState(doc.title);
  const [p, setP] = useState<PrepPayload>(doc.payload);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const change = (next: PrepPayload) => { setP(next); setDirty(true); };
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      onSaved(await editPrepDoc(projectId, rid, doc.id, { title, header: p.header, sections: p.sections, notes: p.notes }));
      setDirty(false);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const download = async () => {
    if (!doc.document_id) return;
    try {
      const r = await getDownloadUrl(doc.document_id);
      window.open(r.download_url, "_blank", "noopener");
    } catch (e) {
      setErr(errorMessage(e));
    }
  };
  const input = "w-full rounded border border-slate-200 px-1.5 py-1 text-sm disabled:border-transparent disabled:bg-transparent";

  return (
    <Box className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <input value={title} disabled={!editable} onChange={(e) => { setTitle(e.target.value); setDirty(true); }}
          className="min-w-0 flex-1 rounded border border-slate-200 px-2 py-1 text-lg font-semibold disabled:border-transparent disabled:bg-transparent" />
        <span className="flex items-center gap-2 text-xs">
          <span className="text-slate-500">v{doc.version}</span>
          {doc.status === "final" ? (
            <button type="button" onClick={download} className="text-blue-700">Download PDF</button>
          ) : (
            <a href={previewUrl(projectId, rid, doc.id)} target="_blank" rel="noopener noreferrer" className="text-blue-700">
              Preview PDF
            </a>
          )}
        </span>
      </div>

      {p.header.length > 0 && (
        <table className="w-full border-collapse text-sm">
          <tbody>
            {p.header.map((h, i) => (
              <tr key={i} className="border-b border-slate-100">
                <td className="w-1/4 py-1 pr-2 font-medium text-slate-700">{h.label}</td>
                <td className="py-1">
                  <input value={h.value} disabled={!editable} className={input}
                    onChange={(e) => change({ ...p, header: p.header.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)) })} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {p.sections.map((s, si) => {
        const setRow = (ri: number, k: string, v: string) =>
          change({ ...p, sections: p.sections.map((x, j) => (j === si ? { ...x, rows: x.rows.map((r, k2) => (k2 === ri ? { ...r, [k]: v } : r)) } : x)) });
        return (
          <div key={si} className="space-y-1.5">
            <h3 className="m-0 text-sm font-semibold text-slate-900">{s.heading}</h3>
            {s.kind === "table" && (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-slate-300 text-left text-xs text-slate-600">
                      <th className="py-1 pr-2">Item</th><th className="py-1 pr-2">Specification</th>
                      <th className="w-20 py-1 pr-2">Qty</th><th className="w-20 py-1 pr-2">Unit</th>
                      <th className="py-1">How it was worked out</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.rows.map((r, ri) => (
                      <tr key={ri} className="border-b border-slate-100 align-top">
                        <td className="py-1 pr-2"><input value={r.item ?? ""} disabled={!editable} className={input} onChange={(e) => setRow(ri, "item", e.target.value)} /></td>
                        <td className="py-1 pr-2"><input value={r.spec ?? ""} disabled={!editable} className={input} onChange={(e) => setRow(ri, "spec", e.target.value)} /></td>
                        <td className="py-1 pr-2"><input value={String(r.qty ?? "")} disabled={!editable} className={`${input} text-right`} onChange={(e) => setRow(ri, "qty", e.target.value)} /></td>
                        <td className="py-1 pr-2"><input value={r.unit ?? ""} disabled={!editable} className={input} onChange={(e) => setRow(ri, "unit", e.target.value)} /></td>
                        <td className="py-1 text-xs text-slate-600">
                          {r.basis}
                          {r.source && <span className="block text-slate-500">Source: {r.source}</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {s.kind === "pairs" && (
              <table className="w-full border-collapse text-sm">
                <tbody>
                  {s.rows.map((r, ri) => (
                    <tr key={ri} className="border-b border-slate-100">
                      <td className="w-1/3 py-1 pr-2"><input value={r.label ?? ""} disabled={!editable} className={input} onChange={(e) => setRow(ri, "label", e.target.value)} /></td>
                      <td className="py-1"><input value={r.value ?? ""} disabled={!editable} className={input} onChange={(e) => setRow(ri, "value", e.target.value)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {(s.kind === "text" || s.text) && (
              <textarea value={s.text ?? ""} disabled={!editable} rows={Math.min(10, (s.text ?? "").split("\n").length + 1)}
                onChange={(e) => change({ ...p, sections: p.sections.map((x, j) => (j === si ? { ...x, text: e.target.value } : x)) })}
                className={`${input} whitespace-pre-wrap`} />
            )}
          </div>
        );
      })}

      {(p.notes || editable) && (
        <div className="space-y-1">
          <h3 className="m-0 text-sm font-semibold text-slate-900">Note at the bottom</h3>
          <textarea value={p.notes ?? ""} disabled={!editable} rows={2} className={input}
            onChange={(e) => change({ ...p, notes: e.target.value })} />
        </div>
      )}

      {err && <p className="m-0 text-sm text-red-700">{err}</p>}
      {editable && dirty && (
        <div className="flex justify-end">
          <button type="button" disabled={busy} onClick={save}
            className="rounded-md border border-slate-900 px-3 py-1.5 text-sm">Save my edits</button>
        </div>
      )}
    </Box>
  );
}

function Correction({ busy, estimate, onSend, onFinalize, onCancel }: {
  busy: boolean; estimate: () => Promise<CostEstimate>; onSend: (c: string, remember: boolean, usd: number) => void;
  onFinalize: () => void; onCancel: () => void;
}) {
  const [load] = useState(() => estimate);
  const [c, setC] = useState("");
  const [remember, setRemember] = useState(false);
  return (
    <Box className="space-y-3 text-sm">
      <Label>Something wrong?</Label>
      <textarea rows={3} value={c} onChange={(e) => setC(e.target.value)}
        placeholder="Say what to change, e.g. stakes are bought ready-made, not cut from 2x4s"
        className="w-full rounded-md border border-slate-300 px-3 py-2" />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-slate-700">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Remember this for every project (a BRIO practice)
        </label>
      </div>
      {c.trim().length >= 2 && (
        <CostApproval load={load} action="Redo" busy={busy} onApprove={(usd) => onSend(c, remember, usd)} />
      )}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-3">
        <button type="button" disabled={busy} onClick={onCancel} className="text-slate-600">Discard request</button>
        <button type="button" disabled={busy} onClick={onFinalize}
          className="rounded-md bg-slate-900 px-4 py-2 text-white disabled:opacity-40">
          Looks right: make the PDFs
        </button>
      </div>
      <p className="m-0 text-xs text-slate-500">The PDFs are filed in this project&rsquo;s Documents (office only). Nothing is sent to anyone.</p>
    </Box>
  );
}
