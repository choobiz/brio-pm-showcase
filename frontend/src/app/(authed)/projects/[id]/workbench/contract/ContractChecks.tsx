"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { amendContract, draftTimeExtension, getContractChecks, setSignedCopy, type ContractChecks as Checks } from "@/lib/contractChecks";
import { uploadDocument } from "@/lib/documents";
import { errorMessage } from "@/lib/workspaceApi";

/** R2Contract: the passed-date banner (drafts a time-extension change order),
 *  the checks, the countersigned copy and Amend. Nothing here changes the
 *  signed contract's terms. */

function day(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-CA", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

export function LateBanner({ projectId }: { projectId: string }) {
  const [c, setC] = useState<Checks | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { getContractChecks(projectId).then(setC, () => setC(null)); }, [projectId]);
  if (!c?.late) return null;
  const l = c.late;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-orange-700 bg-orange-50 px-4 py-3 text-sm text-orange-950">
      <span>
        <b>Substantial completion in the contract: {day(l.contract_date)}. The schedule now says {day(l.expected)}</b> ({l.days} days later).
        {l.passed && " The contract date has passed."}
      </span>
      {l.extension_co || msg ? (
        <span>{msg ?? <>Draft <Link className="underline" href={`/projects/${projectId}/money?view=change_orders`}>CO-{l.extension_co}</Link> extends it.</>}</span>
      ) : (
        <button type="button" disabled={busy} className="rounded border border-orange-900 bg-white px-3 py-1.5 text-xs font-medium"
          onClick={async () => {
            setBusy(true);
            try {
              const co = await draftTimeExtension(projectId);
              setMsg(`Draft CO-${co.number} made (+${co.schedule_impact_days} days, no cost). Add the reasons in Money › Change orders before sending.`);
            } catch (e) { setMsg(errorMessage(e)); } finally { setBusy(false); }
          }}>
          Draft a time-extension change order
        </button>
      )}
    </div>
  );
}

export function ContractChecks({ projectId }: { projectId: string }) {
  const router = useRouter();
  const [c, setC] = useState<Checks | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirmAmend, setConfirmAmend] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => { getContractChecks(projectId).then(setC, (e) => setMsg(errorMessage(e))); }, [projectId]);
  if (!c?.contract) return msg ? <p className="text-sm text-red-700">{msg}</p> : null;
  const signed = c.contract.status === "signed" || c.contract.status === "executed";

  async function upload(f: File) {
    setBusy(true); setMsg(null);
    try {
      const doc = await uploadDocument(projectId, f, "contract", undefined, true);
      setC(await setSignedCopy(projectId, doc.id));
      setMsg("Countersigned copy filed under Documents › Contract.");
    } catch (e) { setMsg(errorMessage(e)); } finally { setBusy(false); }
  }

  return (
    <section className="rounded border border-slate-200 bg-white px-4 py-3">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700">Checks · {c.checks.length}</h2>
      {c.checks.length === 0 ? <p className="mt-2 text-sm text-slate-600">Nothing to flag.</p> : (
        <ul className="mt-2 divide-y divide-slate-100 text-sm">
          {c.checks.map((k) => (
            <li key={k.kind} className="flex flex-wrap items-center gap-2 py-2">
              <span className="rounded border border-orange-700 px-1.5 text-xs font-semibold text-orange-800">!</span>
              <span className="min-w-0 flex-1">{k.text}</span>
              {k.kind === "signed_copy" && (
                <>
                  <input ref={file} type="file" accept="application/pdf,image/*" className="hidden" aria-label="Countersigned contract"
                    onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
                  <button type="button" disabled={busy} className="rounded border border-slate-900 px-2.5 py-1 text-xs" onClick={() => file.current?.click()}>
                    {busy ? "Uploading…" : "Upload the countersigned PDF"}
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {signed && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 text-sm">
          {confirmAmend ? (
            <>
              <span>Make a new draft version copied from the signed one? The signed contract stays as it is.</span>
              <button type="button" disabled={busy} className="rounded bg-[var(--color-brio-primary)] px-3 py-1 text-xs font-medium text-white"
                onClick={async () => {
                  setBusy(true);
                  try { const a = await amendContract(projectId); setMsg(`Draft ${a.reference} made.`); router.refresh(); }
                  catch (e) { setMsg(errorMessage(e)); } finally { setBusy(false); setConfirmAmend(false); }
                }}>Make the draft</button>
              <button type="button" className="text-xs underline" onClick={() => setConfirmAmend(false)}>Cancel</button>
            </>
          ) : (
            <button type="button" className="rounded border border-slate-900 px-3 py-1 text-xs" onClick={() => setConfirmAmend(true)}>Amend (new version)</button>
          )}
        </div>
      )}
      {msg && <p className="mt-2 text-sm text-slate-700" role="status">{msg}</p>}
    </section>
  );
}
