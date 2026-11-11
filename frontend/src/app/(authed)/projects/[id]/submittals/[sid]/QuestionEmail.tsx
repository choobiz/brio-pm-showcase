"use client";

import { useState } from "react";

import { getEmail, gmailHref, markCopied, saveEmail, type Party, type SubmittalEmail } from "@/lib/submittals";

/**
 * One party's question email (wireframe D). Made from the questions as they
 * stand; the PM edits it and copies it to Gmail. Keystone never sends it. Every
 * time, the panel says to keep the project address in BCC (Amir, 2026-10-17),
 * because that is how the email and the reply are filed on this submittal.
 */
export function QuestionEmail({
  projectId,
  sid,
  refTag,
  party,
  questionIds,
  onCopied,
}: {
  projectId: string;
  sid: string;
  refTag: string;
  party: Party;
  questionIds: string[];
  onCopied: () => void;
}) {
  const [e, setE] = useState<SubmittalEmail | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const inp = "w-full rounded border border-slate-300 px-2 py-1 text-[13px]";

  const open = async () => {
    setBusy(true);
    setErr(null);
    try {
      setE(await getEmail(projectId, sid, party));
    } catch (x) {
      setErr(x instanceof Error ? x.message : String(x));
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    if (!e) return null;
    const saved = await saveEmail(projectId, sid, party, {
      to_name: e.to_name,
      to_email: e.to_email,
      subject: e.subject,
      body: e.body,
    });
    setE(saved);
    return saved;
  };

  if (!e)
    return (
      <div className="flex justify-end">
        <button type="button" disabled={busy} onClick={open} className="rounded-md border border-slate-900 px-3 py-1.5 text-sm disabled:opacity-50">
          {busy ? "Opening…" : "Draft the email"}
        </button>
        {err && <span className="ml-2 text-sm text-red-700">{err}</span>}
      </div>
    );

  const stale = e.finding_ids.join() !== questionIds.join();
  return (
    <div className="flex flex-col gap-2 border-t border-slate-100 pt-3 text-[13px]">
      {stale && (
        <p className="m-0 text-xs text-orange-800">
          The questions changed since this draft.{" "}
          <button type="button" onClick={open} className="underline">
            Rebuild it
          </button>{" "}
          before copying it to Gmail.
        </p>
      )}
      <div className="grid grid-cols-[4.5rem_1fr] items-center gap-2">
        <span className="text-xs text-slate-600">To</span>
        <div className="flex gap-2">
          <input className={inp} placeholder="Name" value={e.to_name} onChange={(x) => setE({ ...e, to_name: x.target.value })} />
          <input className={inp} placeholder="email@example.com" value={e.to_email} onChange={(x) => setE({ ...e, to_email: x.target.value })} />
        </div>
        <span className="text-xs text-slate-600">Subject</span>
        <input className={inp} value={e.subject} onChange={(x) => setE({ ...e, subject: x.target.value })} />
      </div>
      <textarea rows={10} className={inp} value={e.body} onChange={(x) => setE({ ...e, body: x.target.value })} />
      <p
        role="note"
        className={`m-0 rounded border px-2 py-1.5 text-xs ${copied ? "border-orange-300 bg-orange-50 text-orange-900" : "border-slate-200 bg-slate-50 text-slate-700"}`}
      >
        <strong>Sending from Gmail:</strong> BCC <span className="font-mono">{e.project_address}</span> so the email and the
        reply are filed on this submittal. Gmail opens with it already in BCC; check it&apos;s still there before you send.
        Replies come only to you, so forward them to the same address.
        {copied && " (The address is copied to your clipboard.)"}
      </p>
      <div className="flex flex-wrap justify-end gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setErr(null);
            try {
              await save();
            } catch (x) {
              setErr(x instanceof Error ? x.message : String(x));
            } finally {
              setBusy(false);
            }
          }}
          className="rounded-md border border-slate-400 px-3 py-1.5"
        >
          Save draft
        </button>
        <a
          href={stale ? undefined : gmailHref(e, refTag)}
          target="_blank"
          rel="noopener noreferrer"
          aria-disabled={stale}
          onClick={(ev) => {
            // A draft built from older questions would record asks it never carried.
            if (stale) {
              ev.preventDefault();
              return;
            }
            setCopied(true);
            navigator.clipboard?.writeText(e.project_address ?? "").catch(() => undefined);
            save()
              .then(() => markCopied(projectId, sid, party))
              .then(onCopied)
              .catch((x) => setErr(x instanceof Error ? x.message : String(x)));
          }}
          className={`rounded-md px-3 py-1.5 text-white ${stale ? "cursor-not-allowed bg-slate-400" : "bg-slate-900"}`}
          title="Opens Gmail with this text and the project address in BCC"
        >
          Copy to Gmail
        </a>
      </div>
      {e.copied_at && (
        <span className="text-xs text-slate-500">
          Copied to Gmail {new Date(e.copied_at).toLocaleDateString("en-CA", { month: "short", day: "numeric" })}
        </span>
      )}
      {err && <span className="text-red-700">{err}</span>}
    </div>
  );
}
