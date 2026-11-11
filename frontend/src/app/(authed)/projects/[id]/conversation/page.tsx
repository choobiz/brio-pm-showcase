"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";

import { CATEGORY_LABELS, createCommunication, deleteCommunication, getInboundAddress, type CommCategory } from "@/lib/communications";
import { getConversation, getScheduleReply, markBooked, type ConvGroup, type Conversation } from "@/lib/conversation";
import { clearMessageDraft, takeMessageDraft } from "@/lib/messageDraft";
import { sendMessage } from "@/lib/messages";
import { useVisiblePolling } from "@/lib/usePolling";
import { errorMessage } from "@/lib/workspaceApi";

/**
 * Client › Conversation (round 2, approved 2026-10-23): the client portal
 * thread and the logged emails, calls and notes in one timeline. The client
 * still sees only portal messages. Replaces Messages + Conversations.
 */

const FILTERS: { key: "all" | ConvGroup | "reply"; label: string }[] = [
  { key: "all", label: "Everything" },
  { key: "client", label: "Client" },
  { key: "engineers", label: "Engineers & inspectors" },
  { key: "suppliers", label: "Suppliers & trades" },
  { key: "reply", label: "Needs a reply" },
];

const CHANNEL: Record<string, string> = {
  portal: "Portal message", email: "Email", sms: "Text", call: "Call", meeting: "Meeting", note: "Note",
};

function when(s: string | null): string {
  if (!s) return "";
  return new Date(s).toLocaleString("en-CA", {
    weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Vancouver",
  });
}

type Mode = "portal" | "email" | "log";

export default function ConversationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [conv, setConv] = useState<Conversation | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const [q, setQ] = useState("");
  const [inbound, setInbound] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("portal");
  const [draft, setDraft] = useState("");
  const [fromDraft, setFromDraft] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notYet, setNotYet] = useState<string[]>([]);
  // Log a call / text / email
  const [logWho, setLogWho] = useState("");
  const [logChannel, setLogChannel] = useState("call");
  const [logDir, setLogDir] = useState<"inbound" | "outbound">("outbound");
  const [logDate, setLogDate] = useState("");
  const [logSubject, setLogSubject] = useState("");

  const load = useCallback(
    () => getConversation(id).then((c) => { setConv(c); setErr(null); }, (e) => setErr(errorMessage(e))),
    [id],
  );
  useEffect(() => { load(); }, [load]);
  useVisiblePolling(load, 20000, true);
  useEffect(() => {
    getInboundAddress(id).then((a) => setInbound(a.address), () => setInbound(null));
    // A draft written on another page (Schedule › Record "Tell the client").
    // It only fills the box; sending is still the Send button here.
    const d = takeMessageDraft(id);
    if (d) { setDraft(d); setFromDraft(true); setMode("portal"); }
  }, [id]);

  const shown = useMemo(() => {
    if (!conv) return [];
    const needle = q.trim().toLowerCase();
    return conv.items.filter((i) =>
      (filter === "all" || (filter === "reply" ? i.needs_reply : i.group === filter))
      && (!needle || `${i.author} ${i.subject ?? ""} ${i.body}`.toLowerCase().includes(needle)));
  }, [conv, filter, q]);

  async function send() {
    const body = draft.trim();
    if (!body) return;
    setBusy(true);
    setErr(null);
    try {
      if (mode === "portal") {
        await sendMessage(id, body);
        if (fromDraft) { clearMessageDraft(id); setFromDraft(false); }
      } else if (mode === "log") {
        const email = /\S+@\S+/.test(logWho) ? logWho.trim() : undefined;
        await createCommunication(id, {
          body, channel: logChannel, direction: logDir,
          subject: logSubject.trim() || undefined,
          occurred_at: logDate || undefined,
          ...(logDir === "inbound"
            ? { from_name: email ? undefined : logWho.trim() || undefined, from_email: email }
            : { to_emails: email ?? (logWho.trim() || undefined) }),
        });
        setLogWho(""); setLogSubject(""); setLogDate("");
      }
      setDraft("");
      await load();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(cid: string) {
    if (!window.confirm("Delete this logged item? The client never saw it; this removes it from the record.")) return;
    setBusy(true);
    try { await deleteCommunication(id, cid); await load(); } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  }

  const to = conv?.client.emails[0] ?? "";
  const mailto = `mailto:${encodeURIComponent(to)}?${new URLSearchParams({
    ...(inbound ? { bcc: inbound } : {}), body: draft,
  }).toString().replace(/\+/g, "%20")}`;
  const chip = (on: boolean) =>
    `inline-flex min-h-9 items-center rounded-full border px-3 text-sm ${on ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-800"}`;
  const seg = (on: boolean) => `px-3 py-2 text-sm ${on ? "bg-slate-900 text-white" : "bg-white text-slate-800"}`;
  const inp = "rounded border border-slate-300 px-2 py-1.5 text-sm";

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Conversation</h1>
        <p className="mt-1 text-sm text-slate-600">
          The client portal thread and the emails, calls and notes logged for this project, newest first.
          The client sees only portal messages.
        </p>
      </header>
      {err && <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{err}</div>}

      <div className="flex flex-wrap items-start gap-5">
        <section className="min-w-0 flex-[999_1_560px] rounded border border-slate-200 bg-white px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap gap-1.5">
              {FILTERS.map((f) => {
                const n = !conv ? 0 : f.key === "all" ? conv.items.length : f.key === "reply" ? conv.needs_reply : conv.counts[f.key];
                return (
                  <button key={f.key} type="button" onClick={() => setFilter(f.key)} className={chip(filter === f.key)}>
                    {f.label}{f.key !== "all" && n > 0 ? ` · ${n}` : ""}
                  </button>
                );
              })}
            </div>
            <input type="search" aria-label="Search the conversation" placeholder="Search" value={q}
              onChange={(e) => setQ(e.target.value)} className={`${inp} min-h-9`} />
          </div>

          <div className="mt-2">
            {!conv && <p className="py-6 text-sm text-slate-500">Loading…</p>}
            {conv && shown.length === 0 && (
              <p className="py-6 text-sm text-slate-500">
                {conv.items.length === 0 ? "Nothing yet. Message the client below, or log a call." : "Nothing matches."}
              </p>
            )}
            {shown.map((i) => (
              <article key={`${i.kind}-${i.id}`} className={`border-b border-slate-100 py-3 last:border-0 ${i.ours ? "sm:ml-12" : ""}`}>
                <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
                  <b className="text-slate-900">{i.author}</b>
                  <span className="rounded border border-slate-300 px-1.5 text-xs">
                    {CHANNEL[i.channel] ?? i.channel}{i.kind === "comm" && i.source && i.source !== "manual" ? " · filed by email" : ""}
                  </span>
                  <span>{when(i.at)}</span>
                  {i.counterpart && <span>to {i.counterpart}</span>}
                  {i.category && i.category !== "general" && (
                    <span className="text-xs text-slate-500">{CATEGORY_LABELS[i.category as CommCategory] ?? i.category}</span>
                  )}
                  {i.needs_reply && <span className="rounded border border-orange-700 px-1.5 text-xs text-orange-800">Needs a reply</span>}
                  {!i.read && <span className="text-xs font-semibold text-blue-700">new</span>}
                  {i.kind === "comm" && (
                    <button type="button" onClick={() => remove(i.id)} disabled={busy}
                      className="ml-auto text-xs text-slate-500 underline">Delete</button>
                  )}
                </div>
                {i.subject && <div className="mt-1 text-sm font-medium">{i.subject}</div>}
                <div className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">{i.body}</div>
                {i.summary && i.summary !== i.body && <div className="mt-1 text-xs text-slate-500">Summary: {i.summary}</div>}
                {i.booking && !notYet.includes(i.id) && (
                  <div className="mt-1 text-xs text-slate-700">
                    Booking {i.booking.ref} ({i.booking.title}) marked booked?{" "}
                    <button type="button" className="font-semibold text-blue-700 underline" disabled={busy}
                      onClick={async () => { setBusy(true); try { await markBooked(id, i.booking!.id); await load(); } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); } }}>Yes</button>
                    {" · "}
                    <button type="button" className="underline" onClick={() => setNotYet((n) => [...n, i.id])}>Not yet</button>
                  </div>
                )}
              </article>
            ))}
          </div>

          <div className="mt-2 space-y-2 border-t border-slate-200 pt-3">
            <div className="inline-flex flex-wrap overflow-hidden rounded border border-slate-900" role="group" aria-label="Reply as">
              <button type="button" className={seg(mode === "portal")} onClick={() => setMode("portal")}>Message the client (portal)</button>
              <button type="button" className={seg(mode === "email")} onClick={() => setMode("email")}>Draft an email</button>
              <button type="button" className={seg(mode === "log")} onClick={() => setMode("log")}>Log a call, text or email</button>
            </div>
            {fromDraft && mode === "portal" && (
              <div className="flex flex-wrap items-center gap-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                Draft from Schedule › Record what happened. Not sent: read it, edit it, then Send.
                <button type="button" className="ml-auto underline"
                  onClick={() => { clearMessageDraft(id); setDraft(""); setFromDraft(false); }}>Discard draft</button>
              </div>
            )}
            {mode === "log" && (
              <div className="flex flex-wrap gap-2">
                <select value={logChannel} onChange={(e) => setLogChannel(e.target.value)} className={inp} aria-label="Kind">
                  <option value="call">Call</option><option value="sms">Text</option><option value="email">Email</option>
                  <option value="meeting">Meeting</option><option value="note">Note</option>
                </select>
                <select value={logDir} onChange={(e) => setLogDir(e.target.value as "inbound" | "outbound")} className={inp} aria-label="Direction">
                  <option value="outbound">From us</option><option value="inbound">From them</option>
                </select>
                <input value={logWho} onChange={(e) => setLogWho(e.target.value)} placeholder={logDir === "inbound" ? "Who (name or email)" : "To (name or email)"}
                  aria-label="Who" className={`${inp} min-w-0 flex-1`} />
                <input type="date" value={logDate} onChange={(e) => setLogDate(e.target.value)} aria-label="When" className={inp} />
                <input value={logSubject} onChange={(e) => setLogSubject(e.target.value)} placeholder="Subject (optional)"
                  aria-label="Subject" className={`${inp} min-w-0 basis-full`} />
              </div>
            )}
            <label className="sr-only" htmlFor="reply">Reply</label>
            <textarea id="reply" rows={fromDraft ? 7 : 3} value={draft} onChange={(e) => setDraft(e.target.value)}
              placeholder={mode === "log" ? "What was said" : "Write a reply"}
              className="block w-full rounded border border-slate-300 px-3 py-2 text-sm" />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs text-slate-500">
                {mode === "portal" && "The client sees this in their portal and gets an email that it's there."}
                {mode === "email" && (to ? `Opens your mail app to ${to}${inbound ? ", with the project address in BCC so it files here" : ""}. Nothing is sent from here.` : "No client email on file; add one under Clients.")}
                {mode === "log" && "Recorded here only. The client never sees logged items."}
              </span>
              {mode === "portal" && (
                <button type="button" disabled={busy} className="rounded border border-slate-900 px-3 py-2 text-sm"
                  onClick={async () => {
                    try { const r = await getScheduleReply(id); if (r.text) setDraft((d) => (d.trim() ? `${d}\n\n${r.text}` : r.text)); }
                    catch (e) { setErr(errorMessage(e)); }
                  }}>Suggest a reply from the schedule</button>
              )}
              {mode === "email" ? (
                <a href={mailto} className="rounded bg-[var(--color-brio-primary)] px-4 py-2 text-sm font-medium text-white">Open in my mail app</a>
              ) : (
                <button type="button" onClick={send} disabled={busy || !draft.trim()}
                  className="rounded bg-[var(--color-brio-primary)] px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
                  {busy ? "Saving…" : mode === "portal" ? "Send to portal" : "Log it"}
                </button>
              )}
            </div>
          </div>
        </section>

        <aside className="flex flex-[1_1_300px] flex-col gap-4">
          <section className="rounded border border-slate-200 bg-white px-4 py-3">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-600">Open questions · {conv?.open_questions.length ?? 0}</h2>
            <ul className="mt-2 space-y-2 text-sm">
              {(conv?.open_questions ?? []).map((o, n) => (
                <li key={`${o.id}-${n}`}>{o.item}<div className="text-xs text-slate-500">from {o.author} · {when(o.at)}</div></li>
              ))}
              {conv && conv.open_questions.length === 0 && <li className="text-slate-500">None waiting on us.</li>}
            </ul>
          </section>
          <section className="rounded border border-slate-200 bg-white px-4 py-3">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-600">Decisions on record · {conv?.decisions.length ?? 0}</h2>
            <ul className="mt-2 space-y-2 text-sm">
              {(conv?.decisions ?? []).map((d, n) => (
                <li key={n}>{d.what}<div className="text-xs text-slate-500">{d.where} · {when(d.at)}</div></li>
              ))}
              {conv && conv.decisions.length === 0 && <li className="text-slate-500">None yet. Approved change orders, selections and an accepted proposal show here.</li>}
            </ul>
          </section>
          <section className="rounded border border-slate-200 bg-white px-4 py-3">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-600">Project email address</h2>
            <div className="mt-2 break-all font-mono text-sm">{inbound ?? "—"}</div>
            <p className="mt-1 text-xs text-slate-500">BCC it, or forward mail to it, and the mail is filed here.</p>
          </section>
        </aside>
      </div>
    </div>
  );
}
