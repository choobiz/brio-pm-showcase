"use client";

import { useEffect, useState } from "react";

import { WorkspaceError } from "@/components/WorkspaceError";
import { defaultPicks, mailtoHref, type EmailOption } from "@/lib/estimateEmail";
import { fmtMoney } from "@/lib/fmt";
import { workspaceGet, workspacePost } from "@/lib/workspaceApi";

type DraftStatus = {
  status: string;
  error: string | null;
  draft: { subject: string; body: string } | null;
  warnings: string[];
  version_ids?: string[];
};
type PastDraft = { job_id: string; status: string; created_at: string | null; subject: string | null; version_ids: string[] };

/**
 * "Draft email to client": the assistant drafts the email that shares the
 * "scope + selections + lump sum" packages; you edit it and send it from your
 * own mail. Keystone sends nothing (Amir, 2026-10-27).
 */
export function EmailEstimateButton({ projectId, vid }: { projectId: string; vid: string }) {
  const base = `/projects/${projectId}/workbench/estimate-email`;
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<EmailOption[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [past, setPast] = useState<PastDraft[]>([]);
  const [shownJob, setShownJob] = useState<string | null>(null);

  // Earlier drafts for this project, newest first (Amir, 2026-10-27): closing
  // the panel used to lose the text.
  const loadPast = () => workspaceGet<PastDraft[]>(`${base}/drafts`).then(setPast).catch(() => setPast([]));
  useEffect(() => {
    if (open) loadPast();
  }, [open]);

  const showPast = async (jobIdToShow: string) => {
    setError(null);
    setFailed(null);
    try {
      const d = await workspaceGet<DraftStatus>(`${base}/drafts/${jobIdToShow}`);
      if (!d.draft) {
        setFailed("That draft didn't finish, so there is nothing to show.");
        return;
      }
      setSubject(d.draft.subject);
      setBody(d.draft.body);
      setWarnings(d.warnings ?? []);
      if (d.version_ids?.length) setPicked(d.version_ids);
      setShownJob(jobIdToShow);
    } catch (e) {
      setError(e);
    }
  };

  useEffect(() => {
    if (!open || options) return;
    workspaceGet<EmailOption[]>(`${base}/options`)
      .then((o) => {
        setOptions(o);
        setPicked(defaultPicks(o, vid));
      })
      .catch(setError);
  }, [open, options, base, vid]);

  // Poll the draft job until it is done or failed.
  useEffect(() => {
    if (!jobId) return;
    let stop = false;
    const tick = async () => {
      let d: DraftStatus;
      try {
        d = await workspaceGet<DraftStatus>(`${base}/drafts/${jobId}`);
      } catch (e) {
        if (!stop) {
          setError(e);
          setJobId(null);
        }
        return;
      }
      if (stop) return;
      if (d.status === "done" && d.draft) {
        setSubject(d.draft.subject);
        setBody(d.draft.body);
        setWarnings(d.warnings ?? []);
        setShownJob(jobId);
        setJobId(null);
        loadPast();
      } else if (d.status === "error") {
        setFailed("The draft didn't come back usable. Press Draft email to try again.");
        setJobId(null);
        loadPast();
      } else {
        setTimeout(tick, 2000);
      }
    };
    tick();
    return () => {
      stop = true;
    };
  }, [jobId, base]);

  const draft = async () => {
    setError(null);
    setFailed(null);
    setWarnings([]);
    setSubject("");
    setBody("");
    try {
      const r = await workspacePost<{ job_id: string }>(`${base}/drafts`, { version_ids: picked, instructions: notes });
      setJobId(r.job_id);
    } catch (e) {
      setError(e);
    }
  };

  const copy = async () => {
    await navigator.clipboard.writeText(`Subject: ${subject}\n\n${body}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const pdfHref = (id: string) => `/api/workbench/estimate-selection-pdf/${projectId}/${id}?mode=scopepack&_cb=${Date.now()}`;

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="rounded-md border border-slate-900 px-3 py-1.5 text-sm">
        ✉ Draft email to client
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/30 p-4" role="dialog" aria-modal="true">
          <div className="mt-8 w-full max-w-2xl rounded-md border border-slate-300 bg-white p-4 shadow-lg">
            <div className="flex items-center justify-between">
              <div className="text-sm font-semibold">Email the estimate package</div>
              <button type="button" onClick={() => setOpen(false)} className="text-sm text-slate-500" aria-label="Close">
                ✕
              </button>
            </div>
            <p className="mt-1 text-[13px] text-slate-600">
              Each estimate goes as its &quot;scope + selections + lump sum&quot; PDF. The assistant drafts the email; you
              edit it and send it from your own mail. Nothing is sent from Keystone.
            </p>

            {past.length > 0 && (
              <details className="mt-3 rounded border border-slate-200" open={!body && !jobId}>
                <summary className="cursor-pointer px-2 py-1.5 text-[13px] font-medium">
                  Previous drafts ({past.length})
                </summary>
                <ul className="max-h-40 overflow-y-auto border-t border-slate-100">
                  {past.map((p) => {
                    const names = p.version_ids
                      .map((id) => options?.find((o) => o.id === id))
                      .map((o) => (o ? `v${o.version_number}` : "?"))
                      .join(" + ");
                    return (
                      <li key={p.job_id}>
                        <button
                          type="button"
                          disabled={p.status !== "done"}
                          onClick={() => showPast(p.job_id)}
                          className={`flex w-full items-baseline gap-2 px-2 py-1.5 text-left text-[13px] hover:bg-slate-50 disabled:cursor-default disabled:text-slate-400 ${
                            shownJob === p.job_id ? "bg-slate-100" : ""
                          }`}
                        >
                          <span className="shrink-0 tabular-nums text-slate-500">
                            {p.created_at ? new Date(p.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : ""}
                          </span>
                          <span className="flex-1 truncate">
                            {p.status === "done" ? p.subject : p.status === "error" ? "failed" : "drafting…"}
                          </span>
                          {names && <span className="shrink-0 text-slate-500">{names}</span>}
                        </button>
                      </li>
                    );
                  })}
                </ul>
                <p className="border-t border-slate-100 px-2 py-1 text-[11px] text-slate-500">
                  Shows each draft as the assistant wrote it; edits made here are not saved.
                </p>
              </details>
            )}

            <div className="mt-3 text-[13px] font-medium">Include</div>
            {!options && error == null && <div className="text-[13px] text-slate-500">Loading estimates…</div>}
            <div className="mt-1 max-h-48 space-y-1 overflow-y-auto">
              {options?.map((o) => (
                <label key={o.id} className="flex items-center gap-2 text-[13px]">
                  <input
                    type="checkbox"
                    checked={picked.includes(o.id)}
                    onChange={(e) => setPicked((p) => (e.target.checked ? [...p, o.id] : p.filter((x) => x !== o.id)))}
                  />
                  <span className="flex-1">
                    v{o.version_number} {o.reference}
                    {o.is_active ? " · current" : ""}
                    <span className="text-slate-500"> · {o.scope_reference ? `scope ${o.scope_reference}` : "no scope linked"}</span>
                  </span>
                  <span className="tabular-nums text-slate-700">{fmtMoney(o.subtotal)} + GST</span>
                </label>
              ))}
            </div>

            <label className="mt-3 block text-[13px] font-medium" htmlFor="email-notes">
              Notes for the draft <span className="font-normal text-slate-500">(optional)</span>
            </label>
            <textarea
              id="email-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
              placeholder="e.g. thank them for the site visit on Oct 22; mention we can start the corridor first"
              className="mt-1 w-full rounded border border-slate-300 p-2 text-[13px]"
            />
            <div className="mt-2 flex justify-end">
              <button
                type="button"
                disabled={picked.length === 0 || !!jobId}
                onClick={draft}
                className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300"
              >
                {jobId ? "Drafting…" : body ? "Draft again" : "Draft email"}
              </button>
            </div>

            {error != null && <WorkspaceError error={error} className="mt-2" />}
            {failed && <div className="mt-2 rounded border border-amber-200 bg-amber-50 p-2 text-[13px] text-amber-900">{failed}</div>}

            {body && (
              <div className="mt-3 border-t border-slate-200 pt-3">
                {warnings.length > 0 && (
                  <ul className="mb-2 list-disc rounded border border-amber-200 bg-amber-50 py-2 pl-6 pr-2 text-[13px] text-amber-900">
                    {warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                )}
                <label className="block text-[13px] font-medium" htmlFor="email-subject">Subject</label>
                <input
                  id="email-subject"
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                  className="mt-1 w-full rounded border border-slate-300 p-2 text-[13px]"
                />
                <label className="mt-2 block text-[13px] font-medium" htmlFor="email-body">Message</label>
                <textarea
                  id="email-body"
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  rows={16}
                  className="mt-1 w-full rounded border border-slate-300 p-2 font-sans text-[13px]"
                />
                <div className="mt-2 text-[13px] font-medium">Attach these PDFs</div>
                <div className="mt-1 flex flex-wrap gap-2">
                  {options
                    ?.filter((o) => picked.includes(o.id))
                    .map((o) => (
                      <a key={o.id} href={pdfHref(o.id)} target="_blank" rel="noopener noreferrer" className="rounded border border-slate-300 px-2 py-1 text-[13px] hover:bg-slate-50">
                        ⤓ v{o.version_number} {o.reference}
                      </a>
                    ))}
                </div>
                <div className="mt-3 flex justify-end gap-2">
                  <button type="button" onClick={copy} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
                    {copied ? "Copied" : "Copy email"}
                  </button>
                  <a href={mailtoHref(subject, body)} className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white">
                    Open in mail app
                  </a>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
