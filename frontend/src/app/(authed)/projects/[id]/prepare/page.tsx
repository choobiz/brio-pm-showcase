"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { use, useCallback, useEffect, useMemo, useState } from "react";

import { CostApproval } from "@/components/CostApproval";
import { Box, Label, Tag } from "@/components/kit";
import { WorkspaceError } from "@/components/WorkspaceError";
import { listDocuments, uploadDocument, type DocumentRead } from "@/lib/documents";
import {
  createPrep,
  estimateNew,
  getBudget,
  listPrep,
  STATUS_LABEL,
  type PrepBudget,
  type PrepListItem,
} from "@/lib/prep";
import { errorMessage } from "@/lib/workspaceApi";

/**
 * Prepare — ask for a takeoff, supplier order, rental request or field sheet,
 * pick the documents it should work from (or attach photos), and it is drafted
 * for you to check (phase 9, approved 2026-10-13).
 *
 * Drafting runs on Amir's Mac when it is on (his Claude subscription, no API
 * cost); otherwise on the server, under the monthly budget.
 */

const EXAMPLES = [
  "Forming lumber order and form rental request from the foundation layout",
  "Framing lumber price request from the structural and architectural drawings",
  "Crew sheet for the footing pour: specs, dates and inspection holds",
];

export default function PreparePage({ params }: { params: Promise<{ id: string }> }) {
  const { id: projectId } = use(params);
  const router = useRouter();
  const q = useSearchParams().get("q") ?? "";
  const [text, setText] = useState(q);
  const [docs, setDocs] = useState<DocumentRead[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [rows, setRows] = useState<PrepListItem[] | null>(null);
  const [budget, setBudget] = useState<PrepBudget | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loadErr, setLoadErr] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoadErr(null);
    listDocuments(projectId).then(setDocs).catch(setLoadErr);
    listPrep(projectId).then(setRows).catch(setLoadErr);
    getBudget().then(setBudget).catch(() => {});
  }, [projectId]);
  useEffect(load, [load]);

  const shown = useMemo(() => {
    const f = filter.trim().toLowerCase();
    const all = (docs ?? []).filter((d) => d.category !== "prepared");
    return f ? all.filter((d) => `${d.filename} ${d.category}`.toLowerCase().includes(f)) : all;
  }, [docs, filter]);

  const toggle = (id: string) =>
    setPicked((p) => {
      const n = new Set(p);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const attach = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    setErr(null);
    try {
      for (const f of Array.from(files)) {
        const d = await uploadDocument(projectId, f, f.type.startsWith("image/") ? "photo" : "other", undefined, true);
        setDocs((all) => [d, ...(all ?? [])]);
        setPicked((p) => new Set(p).add(d.id));
      }
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const pickedKey = Array.from(picked).sort().join(",");
  const loadEstimate = useCallback(
    () => estimateNew(projectId, pickedKey ? pickedKey.split(",") : []),
    [projectId, pickedKey],
  );

  const start = async (approveUsd: number) => {
    if (text.trim().length < 3) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await createPrep(projectId, text.trim(), Array.from(picked), approveUsd);
      router.push(`/projects/${projectId}/prepare/${r.id}`);
    } catch (e) {
      setErr(errorMessage(e));
      setBusy(false);
    }
  };

  if (loadErr && !docs) return <WorkspaceError error={loadErr} onRetry={load} />;

  return (
    <div className="space-y-4">
      <Box className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Label>Prepare a document</Label>
          {budget && (
            <span className="text-xs text-slate-600">
              {budget.mac_available ? "Your Mac is on: no AI cost" : "Mac is off: runs on the server"}
              {" · "}${budget.prep_usd.toFixed(2)} of ${budget.cap_usd.toFixed(0)} used this month
            </span>
          )}
        </div>
        <textarea
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="What do you need? e.g. a forming lumber order and form rental request from the foundation layout"
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
        />
        {!text && (
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((x) => (
              <button key={x} type="button" onClick={() => setText(x)}
                className="rounded border border-slate-200 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50">
                {x}
              </button>
            ))}
          </div>
        )}

        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-medium">
              Work from these documents{" "}
              <span className="font-normal text-slate-600">
                ({picked.size} picked; it can also look through the rest)
              </span>
            </span>
            <label className="cursor-pointer rounded border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50">
              Attach photos or files
              <input type="file" multiple accept="image/*,application/pdf" className="hidden"
                onChange={(e) => attach(e.target.files)} />
            </label>
          </div>
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter documents"
            className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" />
          <ul className="m-0 max-h-72 list-none divide-y divide-slate-100 overflow-y-auto rounded-md border border-slate-200 p-0 text-sm">
            {!docs && <li className="px-3 py-2 text-slate-500">Loading documents…</li>}
            {shown.map((d) => (
              <li key={d.id}>
                <label className="flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-slate-50">
                  <input type="checkbox" checked={picked.has(d.id)} onChange={() => toggle(d.id)} />
                  <span className="min-w-0 flex-1 truncate">{d.filename}</span>
                  <span className="shrink-0 text-xs text-slate-500">{d.category}</span>
                </label>
              </li>
            ))}
            {docs && shown.length === 0 && <li className="px-3 py-2 text-slate-500">No documents match.</li>}
          </ul>
        </div>

        {err && <p className="m-0 text-sm text-red-700">{err}</p>}
        <CostApproval load={loadEstimate} action="Start" busy={busy}
          disabled={text.trim().length < 3} onApprove={start} />
      </Box>

      <Box className="space-y-2">
        <Label>Earlier requests</Label>
        {!rows && <p className="m-0 text-sm text-slate-500">Loading…</p>}
        {rows?.length === 0 && <p className="m-0 text-sm text-slate-600">Nothing prepared on this project yet.</p>}
        <ul className="m-0 list-none divide-y divide-slate-100 p-0 text-sm">
          {rows?.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 py-2">
              <Link href={`/projects/${projectId}/prepare/${r.id}`} className="min-w-0 truncate text-blue-700">
                {r.request}
              </Link>
              <span className="flex shrink-0 items-center gap-2 text-xs text-slate-600">
                {r.engine && <span>{r.engine === "mac" ? "Mac" : "Server"}</span>}
                {r.cost_usd > 0 && <span>${r.cost_usd.toFixed(2)}</span>}
                <Tag tone={r.status === "waiting_user" ? "warn" : r.status === "finalized" ? "ok" : "plain"}>
                  {STATUS_LABEL[r.status]}
                </Tag>
              </span>
            </li>
          ))}
        </ul>
      </Box>
    </div>
  );
}
