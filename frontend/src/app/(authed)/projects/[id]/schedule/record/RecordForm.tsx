"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { recordActual } from "@/lib/actuals";
import { putMessageDraft } from "@/lib/messageDraft";
import {
  NOT_STARTED_REASONS,
  clientDraft,
  entryBody,
  entryProblem,
  type Entry,
  type EntryStatus,
} from "@/lib/schedulePlan";

export type RecordItem = {
  code: string;
  name: string;
  plan: string;          // "Oct 15 – Oct 16", formatted by the server page
  critical: boolean;
  recordedStart: string | null;
  hint: string | null;   // what the site reports say, if anything
};

const STATUS_LABEL: Record<EntryStatus, string> = { done: "Done", started: "Started", not_started: "Not started" };

/** Schedule › Record what happened: one Done / Started / Not started answer per
 *  activity, saved together. Unanswered activities keep their plan. "Tell the
 *  client" only drafts a message into Client › Conversation; nothing is sent from here. */
export default function RecordForm({
  projectId, due, others, today,
}: { projectId: string; due: RecordItem[]; others: RecordItem[]; today: string }) {
  const router = useRouter();
  const [entries, setEntries] = useState<Record<string, Entry>>({});
  const [extra, setExtra] = useState<RecordItem[]>([]);
  const [pick, setPick] = useState("");
  const [tell, setTell] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [done, setDone] = useState<{ count: number; draft: string | null } | null>(null);

  const items = [...due, ...extra];
  const byCode = new Map(items.map((i) => [i.code, i]));
  const answered = Object.keys(entries).filter((c) => byCode.has(c));
  const problems = Object.fromEntries(
    answered.map((c) => [c, entryProblem(entries[c], byCode.get(c)!.recordedStart, today)]),
  );
  const ready = answered.length > 0 && answered.every((c) => !problems[c]);

  const set = (code: string, patch: Partial<Entry>) =>
    setEntries((e) => ({ ...e, [code]: { ...(e[code] ?? { status: "done" }), ...patch } as Entry }));
  const clear = (code: string) => setEntries(({ [code]: _, ...rest }) => rest);

  async function save() {
    setBusy(true);
    setErrors({});
    setDone(null);
    const saved: { name: string; entry: Entry; recordedStart: string | null }[] = [];
    const failed: Record<string, string> = {};
    for (const code of answered) {
      const it = byCode.get(code)!;
      try {
        await recordActual(projectId, code, entryBody(entries[code], it.recordedStart));
        saved.push({ name: it.name, entry: entries[code], recordedStart: it.recordedStart });
      } catch (e) {
        failed[code] = e instanceof Error ? e.message : String(e);
      }
    }
    let draft: string | null = null;
    if (tell && saved.length) {
      draft = clientDraft(saved);
      putMessageDraft(projectId, draft);
    }
    setErrors(failed);
    setEntries((e) => Object.fromEntries(Object.entries(e).filter(([c]) => failed[c])));
    setExtra((x) => x.filter((i) => failed[i.code]));
    setDone({ count: saved.length, draft });
    setBusy(false);
    router.refresh();
  }

  const otherChoices = others.filter((o) => !byCode.has(o.code));

  return (
    <section className="rounded border border-amber-700 bg-white">
      <h2 className="px-4 pt-3 pb-1 text-sm font-semibold uppercase tracking-wide text-slate-700">
        Expected by today, nothing on record · {due.length}
      </h2>
      <p className="px-4 pb-2 text-xs text-slate-600">
        Only what should have started or finished by today is listed. Anything you leave unanswered keeps its planned dates.
      </p>

      {items.length === 0 && (
        <p className="px-4 pb-3 text-sm text-slate-600">Everything due by today has something on record.</p>
      )}

      <ul className="divide-y divide-slate-100">
        {items.map((it) => {
          const e = entries[it.code];
          const prob = e ? problems[it.code] : null;
          return (
            <li key={it.code} className="px-4 py-3">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-medium">{it.name}</span>
                <span className="text-xs text-slate-500">
                  {it.code} · planned {it.plan}{it.critical ? " · critical" : ""}
                </span>
              </div>
              {it.hint && <div className="mt-0.5 text-xs text-slate-600">{it.hint}</div>}

              <div className="mt-2 flex flex-wrap items-center gap-2" role="radiogroup" aria-label={`${it.name} status`}>
                {(Object.keys(STATUS_LABEL) as EntryStatus[]).map((s) => (
                  <button
                    key={s}
                    type="button"
                    role="radio"
                    aria-checked={e?.status === s}
                    onClick={() => (e?.status === s ? clear(it.code) : set(it.code, { status: s }))}
                    className={`rounded border px-3 py-1 text-sm ${
                      e?.status === s ? "border-slate-800 bg-slate-800 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}
                  >
                    {STATUS_LABEL[s]}
                  </button>
                ))}

                {e?.status === "done" && (
                  <>
                    {!it.recordedStart && (
                      <label className="text-sm text-slate-600">
                        started{" "}
                        <input type="date" max={today} value={e.start ?? ""} onChange={(ev) => set(it.code, { start: ev.target.value || undefined })}
                          className="rounded border border-slate-300 px-2 py-0.5 text-sm" />
                      </label>
                    )}
                    <label className="text-sm text-slate-600">
                      finished{" "}
                      <input type="date" max={today} value={e.finish ?? ""} onChange={(ev) => set(it.code, { finish: ev.target.value || undefined })}
                        className="rounded border border-slate-300 px-2 py-0.5 text-sm" />
                    </label>
                  </>
                )}
                {e?.status === "started" && (
                  <>
                    {it.recordedStart ? (
                      <span className="text-sm text-slate-600">started {it.recordedStart} (on record)</span>
                    ) : (
                      <label className="text-sm text-slate-600">
                        started{" "}
                        <input type="date" max={today} value={e.start ?? ""} onChange={(ev) => set(it.code, { start: ev.target.value || undefined })}
                          className="rounded border border-slate-300 px-2 py-0.5 text-sm" />
                      </label>
                    )}
                    <label className="text-sm text-slate-600">
                      <input type="number" min={0} max={99} value={e.percent ?? ""} placeholder="%"
                        onChange={(ev) => set(it.code, { percent: ev.target.value === "" ? undefined : Number(ev.target.value) })}
                        className="w-16 rounded border border-slate-300 px-2 py-0.5 text-sm" /> % done
                    </label>
                  </>
                )}
              </div>

              {e?.status === "not_started" && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className="text-sm text-slate-600">why</span>
                  {NOT_STARTED_REASONS.map((r) => (
                    <button key={r} type="button" onClick={() => set(it.code, { reason: r })}
                      className={`rounded-full border px-2.5 py-0.5 text-xs ${
                        e.reason === r ? "border-amber-700 bg-amber-50 text-amber-900" : "border-slate-300 text-slate-700 hover:bg-slate-50"}`}>
                      {r}
                    </button>
                  ))}
                  <input
                    value={e.reason && !(NOT_STARTED_REASONS as readonly string[]).includes(e.reason) ? e.reason : ""}
                    onChange={(ev) => set(it.code, { reason: ev.target.value })}
                    placeholder="Other…"
                    className="min-w-0 basis-48 flex-1 rounded border border-slate-300 px-2 py-0.5 text-sm"
                  />
                </div>
              )}

              {prob && <div className="mt-1 text-xs text-amber-800">{prob}</div>}
              {errors[it.code] && <div className="mt-1 text-xs text-red-700">Not saved: {errors[it.code]}</div>}
            </li>
          );
        })}
      </ul>

      {otherChoices.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 px-4 py-3 text-sm">
          <span className="text-slate-600">Record another activity</span>
          <select value={pick} onChange={(ev) => setPick(ev.target.value)} className="max-w-full rounded border border-slate-300 px-2 py-1 text-sm">
            <option value="">Choose…</option>
            {otherChoices.map((o) => <option key={o.code} value={o.code}>{o.name} ({o.code}, planned {o.plan})</option>)}
          </select>
          <button type="button" disabled={!pick}
            onClick={() => { const o = others.find((x) => x.code === pick); if (o) setExtra((x) => [...x, o]); setPick(""); }}
            className="rounded border border-slate-300 px-3 py-1 text-sm disabled:opacity-50">Add</button>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 bg-slate-50 px-4 py-3">
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={tell} onChange={(ev) => setTell(ev.target.checked)} />
          Tell the client
        </label>
        <span className="text-xs text-slate-500">
          {tell ? "Drafts a message in Client › Conversation for you to read and send. Nothing is sent from here." : "Nothing goes to the client."}
        </span>
        <button type="button" onClick={save} disabled={!ready || busy}
          className="ml-auto rounded bg-[var(--color-brio-primary)] px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          {busy ? "Saving…" : answered.length ? `Save ${answered.length} answer${answered.length === 1 ? "" : "s"}` : "Save"}
        </button>
      </div>

      {done && (
        <div className="border-t border-slate-200 px-4 py-3 text-sm" role="status">
          <div className="text-green-800">Saved {done.count}.{Object.keys(errors).length ? ` ${Object.keys(errors).length} not saved, see above.` : ""}</div>
          {done.draft && (
            <div className="mt-2">
              <div className="text-slate-700">
                Draft for the client, not sent.{" "}
                <Link href={`/projects/${projectId}/conversation`} className="text-blue-700">Open it in Client › Conversation</Link> to edit and send.
              </div>
              <pre className="mt-1 whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">{done.draft}</pre>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
