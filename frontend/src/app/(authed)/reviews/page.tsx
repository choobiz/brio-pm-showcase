"use client";

// brio-pm — Review solicitation. Amir/Sam enter a happy client (or trade) + a
// note; the AI drafts a humble, personal Google-review ask they can tweak and
// send from their own phone/email (mailto:/sms: deep-link). Shows BRIO's live
// review count + the "write a review" link to paste.

import { useEffect, useState } from "react";

type Stats = { rating: number | null; count: number | null; write_url: string; maps_uri: string | null; ok: boolean; error?: string };
type Req = { id: string; recipient_name: string; person_type: string; channel: string | null; status: string; created_at: string; requester_name: string | null };

const api = (path: string, init?: RequestInit) => fetch(`/api/backend/reviews${path}`, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers || {}) } });

export default function ReviewsPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [recent, setRecent] = useState<Req[]>([]);

  const [name, setName] = useState("");
  const [type, setType] = useState<"customer" | "trade">("customer");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [note, setNote] = useState("");
  const [message, setMessage] = useState("");
  const [variation, setVariation] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const loadStats = () => api("/stats").then((r) => r.json()).then(setStats).catch(() => {});
  const loadRecent = () => api("").then((r) => r.json()).then((d) => setRecent(Array.isArray(d) ? d : [])).catch(() => {});
  useEffect(() => { loadStats(); loadRecent(); }, []);

  async function generate(nextVariation: number) {
    if (!name.trim()) { setErr("Add the recipient's name first."); return; }
    setBusy(true); setErr(null);
    try {
      const r = await api("/generate", { method: "POST", body: JSON.stringify({ recipient_name: name, person_type: type, note, variation: nextVariation }) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Failed to draft");
      setMessage(d.message); setVariation(nextVariation);
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed to draft"); }
    finally { setBusy(false); }
  }

  const writeUrl = stats?.write_url || "";
  const fullMessage = message + (writeUrl ? `\n\n${writeUrl}` : "");
  const mailto = `mailto:${encodeURIComponent(email)}?subject=${encodeURIComponent("A quick favour")}&body=${encodeURIComponent(fullMessage)}`;
  const smsHref = `sms:${encodeURIComponent(phone)}?body=${encodeURIComponent(message + (writeUrl ? " " + writeUrl : ""))}`;

  async function logSend(channel: "email" | "sms") {
    try {
      await api("", { method: "POST", body: JSON.stringify({ recipient_name: name, person_type: type, email, phone, note, message, channel }) });
      loadRecent();
    } catch { /* logging is best-effort; the send still happens via the deep-link */ }
  }

  function reset() { setName(""); setEmail(""); setPhone(""); setNote(""); setMessage(""); setVariation(0); setType("customer"); setErr(null); }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Ask for a review</h1>
        <p className="mt-1 text-sm text-slate-500">Only ask clients you know were happy. The draft suggests talking points — it never writes the review for them.</p>
      </div>

      {/* Live count */}
      <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-white p-4">
        <div>
          <div className="text-sm text-slate-500">BRIO on Google</div>
          <div className="mt-0.5 text-2xl font-semibold text-slate-900">
            {stats?.ok ? <>★ {stats.rating ?? "—"} · {stats.count ?? 0} reviews</> : <span className="text-base font-normal text-slate-400">{stats?.error || "Loading…"}</span>}
          </div>
        </div>
        {writeUrl && <a href={writeUrl} target="_blank" rel="noopener" className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">Open review page ↗</a>}
      </div>

      {/* Composer */}
      <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block"><span className="text-xs font-medium uppercase tracking-wide text-slate-500">Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="First name" className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
          <label className="block"><span className="text-xs font-medium uppercase tracking-wide text-slate-500">Who is it?</span>
            <select value={type} onChange={(e) => setType(e.target.value as "customer" | "trade")} className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm">
              <option value="customer">Customer / homeowner</option><option value="trade">Trade / subcontractor</option>
            </select></label>
          <label className="block"><span className="text-xs font-medium uppercase tracking-wide text-slate-500">Email (optional)</span>
            <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@email.com" className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
          <label className="block"><span className="text-xs font-medium uppercase tracking-wide text-slate-500">Phone (optional)</span>
            <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="604…" className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
        </div>
        <label className="block"><span className="text-xs font-medium uppercase tracking-wide text-slate-500">Note about the job (helps the draft)</span>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="e.g. kitchen reno in North Van, loved the cabinetry, on time" className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>

        <div className="flex flex-wrap gap-2">
          <button onClick={() => generate(0)} disabled={busy} className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-40">{busy ? "Drafting…" : "Generate message"}</button>
          {message && <button onClick={() => generate(variation + 1)} disabled={busy} className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40">↻ Recreate</button>}
        </div>
        {err && <p className="rounded-md border-l-2 border-red-400 bg-red-50 px-3 py-2 text-sm text-red-700">{err}</p>}
      </div>

      {/* Draft + send */}
      {message && (
        <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
          <span className="text-xs font-medium uppercase tracking-wide text-slate-500">Message (edit freely before sending)</span>
          <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={5} className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm" />
          {writeUrl && <p className="text-xs text-slate-400">The review link is appended automatically when you send.</p>}
          <div className="flex flex-wrap gap-2">
            <a href={mailto} onClick={() => logSend("email")} className={`rounded-md px-4 py-2 text-sm font-medium text-white ${email ? "bg-blue-600 hover:bg-blue-500" : "pointer-events-none bg-slate-300"}`}>✉ Send by email</a>
            <a href={smsHref} onClick={() => logSend("sms")} className={`rounded-md px-4 py-2 text-sm font-medium text-white ${phone ? "bg-green-600 hover:bg-green-500" : "pointer-events-none bg-slate-300"}`}>💬 Send by text</a>
            <button onClick={() => { navigator.clipboard?.writeText(fullMessage); }} className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">Copy</button>
            <button onClick={reset} className="ml-auto rounded-md px-3 py-2 text-sm text-slate-500 hover:text-slate-800">Clear</button>
          </div>
        </div>
      )}

      {/* Log */}
      {recent.length > 0 && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-semibold text-slate-700">Recently asked</h2>
          <ul className="mt-2 divide-y divide-slate-100 text-sm">
            {recent.map((r) => (
              <li key={r.id} className="flex items-center justify-between py-2">
                <span className="text-slate-800">{r.recipient_name} <span className="text-slate-400">· {r.person_type}</span></span>
                <span className="text-xs text-slate-400">{r.channel ? `sent · ${r.channel}` : r.status} · {new Date(r.created_at).toLocaleDateString()}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
