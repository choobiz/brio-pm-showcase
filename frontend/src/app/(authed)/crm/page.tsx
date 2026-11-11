"use client";

/**
 * The Week — the only screen that matters.
 *
 * Opens on what needs attention today, never on a contact list. Rows arrive
 * pre-sorted by the API with un-actioned opportunities FIRST: an item with a
 * date is being worked, an item without one is open and going nowhere, which is
 * worse and should be louder.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CrmGuide, useCrmGuide } from "@/components/CrmGuide";
import { draftFor } from "@/components/CrmDraft";
import {
  getQueue, logInteraction, money,
  type Channel, type CrmContactDetail,
  type QueueItem, type QueueReason, type QueueResponse,
} from "@/lib/crm";

// How to reach this firm — the badge answers "what do I do with this row".
//
// Looked up through channelStyle() rather than indexed directly. A frontend
// deployed ahead of its API receives rows with no `channel` at all, and
// CHANNEL[undefined].cls throws during render — which white-screens the entire
// page over one absent field. A queue row missing a badge is a far better
// failure than a queue you cannot open.
const CHANNEL: Record<Channel, { label: string; cls: string }> = {
  call:         { label: "Call",         cls: "bg-emerald-100 text-emerald-900 border-emerald-200" },
  email:        { label: "Email",        cls: "bg-sky-100 text-sky-900 border-sky-200" },
  website:      { label: "Via website",  cls: "bg-violet-100 text-violet-900 border-violet-200" },
  find_contact: { label: "Find contact", cls: "bg-slate-200 text-slate-800 border-slate-300" },
};

const CHANNEL_FALLBACK = { label: "Contact", cls: "bg-slate-100 text-slate-700 border-slate-200" };

function channelStyle(ch: Channel | undefined | null) {
  return (ch && CHANNEL[ch]) || CHANNEL_FALLBACK;
}

const REASON_STYLE: Record<QueueReason, { label: string; cls: string }> = {
  no_action: { label: "no next action", cls: "bg-amber-100 text-amber-900 border-amber-200" },
  overdue:   { label: "overdue",        cls: "bg-rose-100 text-rose-900 border-rose-200" },
  due_today: { label: "due today",      cls: "bg-sky-100 text-sky-900 border-sky-200" },
  upcoming:  { label: "upcoming",       cls: "bg-slate-100 text-slate-700 border-slate-200" },
};

const TIER_CLS: Record<string, string> = {
  A: "bg-slate-900 text-white",
  B: "bg-slate-700 text-white",
  C: "bg-slate-300 text-slate-800",
  D: "bg-slate-200 text-slate-600",
};

function todayPlus(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export default function CrmQueuePage() {
  const [data, setData] = useState<QueueResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [mineOnly, setMineOnly] = useState(false);
  const [horizon, setHorizon] = useState(0);
  const guide = useCrmGuide();

  const load = useCallback(() => {
    setLoading(true);
    getQueue({ owner: mineOnly ? "me" : undefined, horizonDays: horizon })
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [mineOnly, horizon]);

  useEffect(load, [load]);

  const counts = data?.counts;
  return (
    <div className="max-w-5xl">
      <CrmGuide open={guide.open} onClose={() => guide.setOpen(false)} />
      <div className="mb-1 flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">This week</h1>
        <div className="flex flex-wrap items-center justify-end gap-2 text-sm">
          <Link
            href="/crm/card-leads"
            className="rounded border border-slate-300 px-2 py-1 text-sm text-slate-600 hover:bg-slate-50"
          >
            Card leads
          </Link>
          <button
            onClick={() => guide.setOpen(true)}
            className="rounded border border-slate-300 px-2 py-1 text-sm text-slate-600 hover:bg-slate-50"
            title="How this screen works"
          >
            Guide
          </button>
          <label className="flex items-center gap-1.5 text-slate-600">
            <input type="checkbox" checked={mineOnly}
                   onChange={(e) => setMineOnly(e.target.checked)} />
            Mine only
          </label>
          <select
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={horizon}
            onChange={(e) => setHorizon(Number(e.target.value))}
          >
            <option value={0}>Due now</option>
            <option value={7}>Next 7 days</option>
            <option value={30}>Next 30 days</option>
          </select>
        </div>
      </div>

      {counts && (
        <p className="mb-5 text-sm text-slate-600">
          {counts.overdue > 0 && (
            <span className="font-medium text-rose-700">{counts.overdue} overdue</span>
          )}
          {counts.overdue > 0 && " · "}
          {counts.due_today} due today
          {counts.no_action > 0 && (
            <> · <span className="font-medium text-amber-800">
              {counts.no_action} with no next action
            </span></>
          )}
          {" — "}
          <span className="text-slate-500">
            {counts.call ?? 0} to call · {counts.email ?? 0} to email
            {(counts.find_contact ?? 0) > 0 &&
              ` · ${counts.find_contact} need a contact found first`}
          </span>
        </p>
      )}

      {loading && <p className="text-sm text-slate-500">Loading…</p>}
      {error && <p className="text-sm text-rose-700">{error}</p>}

      {data && data.items.length === 0 && !loading && (
        <div className="rounded border border-slate-200 bg-white p-8 text-center">
          <p className="font-medium">Nothing due.</p>
          <p className="mt-1 text-sm text-slate-600">
            Widen the horizon above, or{" "}
            <Link href="/crm/contacts" className="underline">pull more firms in</Link>.
          </p>
        </div>
      )}

      <ul className="space-y-2">
        {data?.items.map((it) => (
          <QueueRow
            key={it.opportunity.id}
            item={it}
            open={openId === it.opportunity.id}
            onToggle={() =>
              setOpenId(openId === it.opportunity.id ? null : it.opportunity.id)}
            onLogged={() => { setOpenId(null); load(); }}
          />
        ))}
      </ul>
    </div>
  );
}

function QueueRow({ item, open, onToggle, onLogged }: {
  item: QueueItem; open: boolean; onToggle: () => void; onLogged: () => void;
}) {
  const { contact: c, opportunity: o } = item;
  const style = REASON_STYLE[item.reason] ?? REASON_STYLE.upcoming;
  const [script, setScript] = useState(false);
  return (
    <li className="rounded border border-slate-200 bg-white">
      <div className="flex flex-wrap items-start gap-3 p-3">
        <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded text-xs font-bold ${TIER_CLS[c.tier ?? "D"]}`}>
          {c.tier ?? "–"}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/crm/contacts/${c.id}`}
                  className="truncate font-medium hover:underline">
              {c.name}
            </Link>
            <span className={`rounded border px-1.5 py-0.5 text-[11px] ${channelStyle(item.channel).cls}`}>
              {channelStyle(item.channel).label}
            </span>
            <span className={`rounded border px-1.5 py-0.5 text-[11px] ${style.cls}`}>
              {style.label}{item.days_overdue > 0 && ` · ${item.days_overdue}d`}
            </span>
          </div>

          <p className="mt-0.5 text-xs text-slate-500">
            {c.permits_total ?? 0} permits · {money(c.value_as_applicant)}
            {c.top_use_category && ` · ${c.top_use_category}`}
            {c.cities && ` · ${c.cities.replace(/\|/g, ", ")}`}
          </p>

          {/* The thing to actually do, and what to do it with. */}
          {item.channel_target ? (
            <p className="mt-1 text-sm">
              <span className="font-medium">{item.channel_target}</span>
              {c.contact_confidence === "medium" && (
                <span className="ml-1.5 rounded bg-amber-50 px-1 text-xs text-amber-800">
                  unverified — check before sending
                </span>
              )}
            </p>
          ) : (
            <p className="mt-1 text-sm text-slate-700">{item.channel_hint}</p>
          )}

          {item.channel_target && item.channel_hint && (
            <p className="mt-0.5 text-xs text-slate-500">{item.channel_hint}</p>
          )}

          {/* Once an opportunity has been worked, YOUR next action replaces the
              generic channel guidance — that note is more specific than
              anything derivable from the contact record. */}
          {o.status !== "new" && o.next_action && (
            <p className="mt-1 text-sm font-medium">→ {o.next_action}</p>
          )}

          {item.interaction_count > 0 && (
            <p className="mt-1 text-xs text-slate-500">
              {item.interaction_count} logged
              {item.last_interaction?.outcome && ` · last: ${item.last_interaction.outcome}`}
            </p>
          )}
        </div>

        <div className="flex shrink-0 gap-2">
          {item.channel !== "find_contact" && (
            <Button variant="secondary" onClick={() => setScript(!script)}>
              {script ? "Hide" : "Script"}
            </Button>
          )}
          <Button variant="secondary" onClick={onToggle}>
            {open ? "Cancel" : "Log"}
          </Button>
        </div>
      </div>

      {script && (
        <div className="border-t border-slate-200 bg-slate-50 p-3">
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-3 font-mono text-xs leading-relaxed">
{draftFor(c as unknown as CrmContactDetail).body}
          </pre>
          <button
            onClick={() => navigator.clipboard?.writeText(draftFor(c as unknown as CrmContactDetail).body)}
            className="mt-2 rounded bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700"
          >
            Copy
          </button>
        </div>
      )}

      {open && <LogForm oppId={o.id} onDone={onLogged} />}
    </li>
  );
}

function LogForm({ oppId, onDone }: { oppId: string; onDone: () => void }) {
  const [channel, setChannel] = useState("call");
  const [summary, setSummary] = useState("");
  const [outcome, setOutcome] = useState("");
  const [nextAction, setNextAction] = useState("");
  const [nextAt, setNextAt] = useState(todayPlus(7));
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const closing = status === "won" || status === "lost" || status === "dormant";

  async function submit() {
    setSaving(true); setErr(null);
    try {
      await logInteraction(oppId, {
        channel, summary: summary || undefined, outcome: outcome || undefined,
        status: status || undefined,
        // A closed opportunity needs no next step; an open one always does.
        next_action: closing ? undefined : (nextAction || undefined),
        next_action_at: closing ? undefined : nextAt,
      });
      onDone();
    } catch (e) {
      setErr(String(e)); setSaving(false);
    }
  }

  return (
    <div className="border-t border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap gap-2">
        <select className="rounded border border-slate-300 px-2 py-1 text-sm"
                value={channel} onChange={(e) => setChannel(e.target.value)}>
          {["call", "email", "meeting", "event", "note"].map((x) =>
            <option key={x} value={x}>{x}</option>)}
        </select>
        <Input className="min-w-[14rem] flex-1" placeholder="What happened?"
               value={summary} onChange={(e) => setSummary(e.target.value)} />
        <Input className="min-w-[14rem] flex-1" placeholder="Outcome"
               value={outcome} onChange={(e) => setOutcome(e.target.value)} />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <select className="rounded border border-slate-300 px-2 py-1 text-sm"
                value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Keep status</option>
          {["working", "engaged", "quoting", "won", "lost", "dormant"].map((x) =>
            <option key={x} value={x}>{x}</option>)}
        </select>

        {/* The next action lives in the SAME form as the log on purpose.
            Recording a call without deciding the next step is how a pipeline
            goes quiet without anyone noticing. */}
        {!closing && (
          <>
            <Input className="min-w-[14rem] flex-1" placeholder="Next action"
                   value={nextAction} onChange={(e) => setNextAction(e.target.value)} />
            <input type="date" value={nextAt}
                   onChange={(e) => setNextAt(e.target.value)}
                   className="rounded border border-slate-300 px-2 py-1 text-sm" />
          </>
        )}
        {closing && (
          <span className="text-xs text-slate-500">
            Closing as “{status}” — no next action needed.
          </span>
        )}

        <Button onClick={submit} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
      {err && <p className="mt-2 text-xs text-rose-700">{err}</p>}
    </div>
  );
}
