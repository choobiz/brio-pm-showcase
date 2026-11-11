"use client";

import { useEffect, useRef, useState } from "react";

import { usePortal } from "../portalCtx";
import { getMessages, sendMessage, type PortalMessage } from "@/lib/portal";
import { useVisiblePolling } from "@/lib/usePolling";

function fmtTime(s: string | null): string {
  if (!s) return "";
  return new Date(s).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export default function MessagesPage() {
  const { project, loading } = usePortal();
  const [msgs, setMsgs] = useState<PortalMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  const load = (pid: string) => getMessages(pid).then((r) => setMsgs(r.messages)).catch(() => setMsgs([]));
  useEffect(() => {
    if (!project) return;
    load(project.id);
  }, [project]);
  // Only while the tab is in front — see the note in the staff messages page.
  useVisiblePolling(() => {
    if (project) void load(project.id);
  }, 20000, !!project);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs.length]);

  async function send() {
    const body = draft.trim();
    if (!body || !project) return;
    setBusy(true);
    try {
      await sendMessage(project.id, body);
      setDraft("");
      await load(project.id);
    } finally {
      setBusy(false);
    }
  }

  if (loading || !project) return <div className="h-40" />;

  return (
    <div className="flex flex-col" style={{ minHeight: "calc(100vh - 12rem)" }}>
      <h1 className="text-2xl font-semibold mb-1" style={{ color: "#1D3146" }}>Messages</h1>
      <p className="text-sm text-slate-500 mb-4">Questions for the BRIO team? Message us here anytime.</p>

      <div className="flex-1 space-y-3">
        {msgs.length === 0 && (
          <p className="text-sm text-slate-400 text-center mt-8">
            No messages yet. We&rsquo;re here — send us a note below.
          </p>
        )}
        {msgs.map((m) => {
          const mine = m.sender_role === "owner";
          return (
            <div key={m.id} className={`flex ${mine ? "justify-end" : "justify-start"}`}>
              <div
                className="max-w-[80%] rounded-2xl px-4 py-2.5 text-sm shadow-sm"
                style={mine ? { background: "#0068A0", color: "#fff" } : { background: "#fff", border: "1px solid #ece8e2", color: "#26303a" }}
              >
                <div className="whitespace-pre-wrap">{m.body}</div>
                <div className="mt-1 text-[10px]" style={{ color: mine ? "rgba(255,255,255,0.7)" : "#93a0ad" }}>
                  {mine ? "You" : "BRIO"} · {fmtTime(m.created_at)}
                </div>
              </div>
            </div>
          );
        })}
        <div ref={endRef} />
      </div>

      <div className="sticky bottom-24 mt-3 flex items-end gap-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={2}
          placeholder="Write a message…"
          className="flex-1 resize-none rounded-2xl border px-4 py-2.5 text-sm"
          style={{ borderColor: "#ece8e2", background: "#fff" }}
        />
        <button
          onClick={send}
          disabled={busy || !draft.trim()}
          className="rounded-2xl px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
          style={{ background: "#0068A0" }}
        >
          {busy ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
