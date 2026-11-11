"use client";

import { useEffect, useState } from "react";

import { listDocuments, type DocumentRead } from "@/lib/documents";
import {
  getMilestoneEmail,
  requestDraft,
  saveMilestoneEmail,
  type Milestone,
  type MilestoneEmail,
} from "@/lib/milestones";

/**
 * Wireframe F (approved 2026-10-16): the assistant drafts the email from what
 * the project holds; the PM edits it. Keystone never sends it: Amir sends from his
 * own Gmail (2026-10-17, "I will send the emails myself"), so the only way out is
 * Copy to Gmail, with the project address in BCC and a reminder to keep it there.
 */
export function EmailPanel({ projectId, milestone, onClose }: { projectId: string; milestone: Milestone; onClose: () => void }) {
  const [docs, setDocs] = useState<DocumentRead[]>([]);
  const [attach, setAttach] = useState<Set<string>>(new Set());
  const [instructions, setInstructions] = useState("");
  const [email, setEmail] = useState<MilestoneEmail | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [gmailOpened, setGmailOpened] = useState(false);

  // Reopen the newest unsent draft rather than starting another.
  useEffect(() => {
    const open = milestone.emails.find((e) => e.status === "draft" || e.status === "drafting");
    if (open) getMilestoneEmail(projectId, open.id).then(setEmail).catch(() => undefined);
  }, [milestone, projectId]);

  useEffect(() => {
    listDocuments(projectId)
      .then((d) => setDocs(d.filter((x) => ["drawing", "survey", "permit", "report", "contract"].includes(x.category))))
      .catch(() => setDocs([]));
  }, [projectId]);

  // While the assistant writes, read the draft back until it's ready.
  useEffect(() => {
    if (!email || email.status !== "drafting") return;
    const t = setInterval(async () => {
      try {
        const e = await getMilestoneEmail(projectId, email.id);
        if (e.status !== "drafting") {
          setEmail(e);
          if (e.error) setErr(e.error);
        }
      } catch {
        /* keep polling */
      }
    }, 2000);
    return () => clearInterval(t);
  }, [email, projectId]);

  const draft = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await requestDraft(projectId, milestone.id, {
        attachment_ids: Array.from(attach),
        instructions: instructions.trim() || undefined,
      });
      setEmail(await getMilestoneEmail(projectId, r.email_id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const save = async (patch: Partial<MilestoneEmail>) => {
    if (!email) return;
    setEmail({ ...email, ...patch });
  };

  const persist = async () => {
    if (!email) return null;
    const e = await saveMilestoneEmail(projectId, email.id, {
      to_email: email.to_email ?? "",
      to_name: email.to_name ?? "",
      cc: email.cc ?? "",
      subject: email.subject,
      body: email.body,
      attachment_ids: email.attachment_ids,
    });
    setEmail(e);
    return e;
  };

  const gmailHref = email
    ? `https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(email.to_email ?? "")}&cc=${encodeURIComponent(email.cc ?? "")}&bcc=${encodeURIComponent(email.project_address)}&su=${encodeURIComponent(email.subject_as_sent ?? email.subject)}&body=${encodeURIComponent(email.body)}`
    : "#";
  const placeholder = email ? /\[confirm/i.test(`${email.subject} ${email.body}`) : false;
  const inp = "w-full rounded border border-slate-300 px-2 py-1 text-sm";
  const docName = (id: string) => docs.find((d) => d.id === id)?.filename ?? "a document";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true">
      <div className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-md bg-white p-5 shadow-lg">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="m-0 text-lg font-semibold">
            Email · {milestone.title} <span className="text-xs font-normal text-slate-500">[KS-{milestone.ref}]</span>
          </h2>
          <button type="button" onClick={onClose} className="text-sm text-slate-600">
            Close
          </button>
        </div>

        {!email ? (
          <div className="mt-3 space-y-3 text-sm">
            <p className="m-0 text-slate-600">
              The assistant writes it from the project: the address and permit, the dates on the schedule, what{" "}
              {milestone.party?.company || "they"} need{milestone.party ? "" : "s"} and what the inspector will ask for.
              You read and edit it, then send it from your Gmail.
            </p>
            {!milestone.party?.email && (
              <p className="m-0 text-orange-800">
                No email is set for this role yet. You can add the recipient on the draft, or set it once in Project team.
              </p>
            )}
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Attach</div>
              <div className="max-h-40 overflow-y-auto">
                {docs.map((d) => (
                  <label key={d.id} className="flex items-center gap-2 py-0.5 text-[13px]">
                    <input
                      type="checkbox"
                      checked={attach.has(d.id)}
                      onChange={(e) =>
                        setAttach((s) => {
                          const n = new Set(s);
                          if (e.target.checked) n.add(d.id);
                          else n.delete(d.id);
                          return n;
                        })
                      }
                    />
                    {d.filename}
                  </label>
                ))}
              </div>
            </div>
            <label className="block text-xs text-slate-600">
              Anything to add (optional)
              <textarea
                rows={2}
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                placeholder="e.g. ask whether one visit can cover the wall forms too"
                className={inp}
              />
            </label>
            <button type="button" disabled={busy} onClick={draft} className="rounded-md bg-slate-900 px-3 py-1.5 text-white disabled:opacity-50">
              {busy ? "Starting…" : "Draft it"}
            </button>
          </div>
        ) : email.status === "drafting" ? (
          <p className="mt-4 text-sm text-slate-600">Writing the draft…</p>
        ) : (
          <div className="mt-3 space-y-2 text-sm">
            <div className="grid grid-cols-[5rem_1fr] items-center gap-2">
              <span className="text-xs text-slate-600">To</span>
              <div className="flex gap-2">
                <input className={inp} placeholder="Name" value={email.to_name ?? ""} onChange={(e) => save({ to_name: e.target.value })} />
                <input className={inp} placeholder="email@example.com" value={email.to_email ?? ""} onChange={(e) => save({ to_email: e.target.value })} />
              </div>
              <span className="text-xs text-slate-600">Cc</span>
              <input className={inp} value={email.cc ?? ""} onChange={(e) => save({ cc: e.target.value })} />
              <span className="text-xs text-slate-600">Subject</span>
              <input className={inp} value={email.subject} onChange={(e) => save({ subject: e.target.value })} />
            </div>
            <textarea rows={16} className={`${inp} font-[inherit]`} value={email.body} onChange={(e) => save({ body: e.target.value })} />
            <p className="m-0 text-xs text-slate-500">
              Attached: {email.attachment_ids.length ? email.attachment_ids.map(docName).join(", ") : "nothing"} · BCC and Reply-To:{" "}
              {email.project_address}
            </p>
            {placeholder && <p className="m-0 text-xs text-orange-800">Fill in each [confirm …] before sending.</p>}
            {/* Amir, 2026-10-17: he sends from his own Gmail; remind him every time to
                BCC the project address, which is how the email is filed on the job. */}
            <p
              role="note"
              className={`m-0 rounded border px-2 py-1.5 text-xs ${gmailOpened ? "border-orange-300 bg-orange-50 text-orange-900" : "border-slate-200 bg-slate-50 text-slate-700"}`}
            >
              <strong>Sending from Gmail:</strong> BCC <span className="font-mono">{email.project_address}</span> so the email is
              filed on this project. Gmail opens with it already in BCC; check it's still there before you send. Replies come
              only to you, so forward them to the same address.
              {gmailOpened && " (The address is copied to your clipboard.)"}
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <a
                href={gmailHref}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => {
                  setGmailOpened(true);
                  navigator.clipboard?.writeText(email.project_address).catch(() => undefined);
                }}
                className="rounded-md border border-slate-400 px-3 py-1.5"
                title="Opens Gmail with this text and the project address in BCC; attach files there"
              >
                Copy to Gmail
              </a>
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  setErr(null);
                  try {
                    await persist();
                  } catch (e) {
                    setErr(e instanceof Error ? e.message : String(e));
                  }
                }}
                className="rounded-md border border-slate-900 px-3 py-1.5"
              >
                Save draft
              </button>
            </div>
          </div>
        )}

        {err && <p className="mt-3 text-sm text-red-700">{err}</p>}
      </div>

    </div>
  );
}
