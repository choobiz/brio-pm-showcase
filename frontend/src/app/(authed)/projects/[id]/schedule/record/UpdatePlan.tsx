"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { saveReplan, type ReplanSaved } from "@/lib/actuals";
import { putMessageDraft } from "@/lib/messageDraft";
import { daysBetween, finishDraft } from "@/lib/schedulePlan";
import type { ReplanChange, ReplanPreview } from "@/lib/workbench";

const STATE_LABEL: Record<ReplanChange["state"], string> = {
  done: "done (recorded)",
  assumed: "assumed done",
  started: "under way (recorded)",
  kept: "taken as planned",
  kept_started: "taken as planned, under way",
  planned: "planned",
};

function d(iso: string | null): string {
  if (!iso) return "-";
  const [y, m, day] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, day)).toLocaleDateString("en-CA", {
    weekday: "short", month: "short", day: "numeric", timeZone: "UTC",
  });
}

function shift(n: number | null): string {
  if (n == null || n === 0) return "";
  return n > 0 ? `+${n} d` : `${n} d`;
}

/** Schedule › Update the plan. Shows what a new version built from the records
 *  would move, under either rule for unanswered work, and saves it only on
 *  "Save version N". The client sees it only if "Tell the client" is ticked. */
export default function UpdatePlan({
  projectId, keep, push, answerable,
}: { projectId: string; keep: ReplanPreview; push: ReplanPreview; answerable: string[] }) {
  const router = useRouter();
  const [rule, setRule] = useState<"keep" | "push">("keep");
  const [note, setNote] = useState("");
  const [tell, setTell] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<{ out: ReplanSaved; draft: string | null } | null>(null);

  const p = rule === "keep" ? keep : push;
  const next = p.base.version_number + 1;
  const delta = daysBetween(p.base.p50_finish_date, p.p50_finish_date);
  const changes = [...p.changes].sort((a, b) =>
    Number(b.is_critical) - Number(a.is_critical) || a.new_start.localeCompare(b.new_start));
  const unanswered = keep.kept.length;
  // Inspections and other milestones can't be answered on this page; they
  // are tracked as bookings, so say where they went.
  const elsewhere = keep.kept.filter((k) => !answerable.includes(k.activity_code));

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const out = await saveReplan(projectId, {
        base_schedule_id: p.base.id, unanswered: rule, client_visible: tell, note: note.trim() || undefined,
      });
      let draft: string | null = null;
      if (tell) {
        draft = finishDraft(p.client_p50_finish_date ?? out.previous_p50_finish_date, out.p50_finish_date);
        putMessageDraft(projectId, draft);
      }
      setSaved({ out, draft });
      router.refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (saved) {
    return (
      <section className="rounded border border-green-700 bg-white px-4 py-3 text-sm" role="status">
        <div className="font-semibold text-green-800">
          Saved as version {saved.out.version_number}. Finish {d(saved.out.previous_p50_finish_date)} → {d(saved.out.p50_finish_date)}.
        </div>
        <div className="mt-1 text-slate-700">
          Version {saved.out.version_number - 1} is unchanged.{" "}
          {saved.out.client_visible
            ? "The client portal now shows the new dates."
            : "The client portal still shows the version they had."}{" "}
          <Link href={`/projects/${projectId}/workbench/schedule`} className="text-blue-700">Open the plan</Link>
        </div>
        {saved.draft && (
          <div className="mt-2">
            Message to the client drafted, not sent.{" "}
            <Link href={`/projects/${projectId}/conversation`} className="text-blue-700">Open it in Client › Conversation</Link> to edit and send.
            <pre className="mt-1 whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-700">{saved.draft}</pre>
          </div>
        )}
      </section>
    );
  }

  return (
    <section className="rounded border border-slate-300 bg-white">
      <h2 className="px-4 pt-3 pb-1 text-sm font-semibold uppercase tracking-wide text-slate-700">Update the plan</h2>
      <p className="px-4 text-sm text-slate-700">
        Saves as <b>version {next}</b>, recorded to {d(p.data_date)}. Finish{" "}
        <b>{d(p.base.p50_finish_date)} → {d(p.p50_finish_date)}</b>
        {delta !== 0 && <span className={delta > 0 ? "text-amber-800" : "text-green-800"}> ({delta > 0 ? "+" : ""}{delta} days)</span>}
        , likely by {d(p.p80_finish_date)}. Version {p.base.version_number} stays as it was.
      </p>

      {unanswered > 0 && (
        <fieldset className="mx-4 mt-3 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <legend className="px-1 text-xs font-semibold uppercase tracking-wide">
            {unanswered} activit{unanswered === 1 ? "y" : "ies"} due by today with no answer
          </legend>
          <label className="flex items-start gap-2">
            <input type="radio" name="rule" checked={rule === "keep"} onChange={() => setRule("keep")} className="mt-1" />
            <span>
              Keep their planned dates (taken to have gone to plan): finish <b>{d(keep.p50_finish_date)}</b>
            </span>
          </label>
          <label className="mt-1 flex items-start gap-2">
            <input type="radio" name="rule" checked={rule === "push"} onChange={() => setRule("push")} className="mt-1" />
            <span>
              Nothing happened beyond what&apos;s recorded: finish <b>{d(push.p50_finish_date)}</b>
            </span>
          </label>
          {elsewhere.length > 0 && (
            <p className="mt-1 text-xs">
              {elsewhere.length} of them {elsewhere.length === 1 ? "is an inspection" : "are inspections"} ({elsewhere.map((k) => k.activity_code).join(", ")}),
              not on the list above: they are tracked on{" "}
              <Link href={`/projects/${projectId}/milestones`} className="underline">Bookings &amp; letters</Link>.
            </p>
          )}
          <p className="mt-1 text-xs">Answering them above narrows this. The saved version&apos;s notes list which rule was used and for what.</p>
        </fieldset>
      )}

      <details className="mt-3 border-t border-slate-100" open={changes.length <= 12}>
        <summary className="cursor-pointer px-4 py-2 text-sm font-semibold text-slate-700">
          What moves · {changes.length} ({p.unchanged} unchanged)
        </summary>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-600">
              <tr>
                <th className="px-3 py-2 text-left">Activity</th>
                <th className="px-3 py-2 text-left">Version {p.base.version_number}</th>
                <th className="px-3 py-2 text-left">Version {next}</th>
                <th className="px-3 py-2 text-left">Finish</th>
                <th className="px-3 py-2 text-left">Why</th>
              </tr>
            </thead>
            <tbody>
              {changes.map((c) => (
                <tr key={c.activity_code} className="border-t border-slate-100">
                  <td className="px-3 py-1.5">
                    {c.name} <span className="text-xs text-slate-500">{c.activity_code}</span>
                    {c.is_critical && <span className="ml-1 text-xs text-red-700">critical</span>}
                    {c.is_critical !== c.was_critical && (
                      <span className="ml-1 text-xs text-slate-500">{c.is_critical ? "(newly critical)" : "(off critical path)"}</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 whitespace-nowrap">{d(c.old_start)} – {d(c.old_finish)}</td>
                  <td className="px-3 py-1.5 whitespace-nowrap">{d(c.new_start)} – {d(c.new_finish)}</td>
                  <td className="px-3 py-1.5 whitespace-nowrap text-xs">{shift(c.finish_moved_days)}</td>
                  <td className="px-3 py-1.5 text-xs text-slate-600">{STATE_LABEL[c.state]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      <div className="border-t border-slate-100 px-4 py-3">
        <label className="block text-sm text-slate-700">
          Note for the version (optional)
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
            className="mt-1 block w-full rounded border border-slate-300 px-2 py-1 text-sm" />
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 bg-slate-50 px-4 py-3">
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={tell} onChange={(e) => setTell(e.target.checked)} />
          Tell the client
        </label>
        <span className="text-xs text-slate-500">
          {tell
            ? "The portal shows the new finish date as soon as you save, and a message is drafted in Client › Conversation for you to send."
            : `The client keeps seeing ${p.client_p50_finish_date ? d(p.client_p50_finish_date) : "what they have now"}.`}
        </span>
        <button type="button" onClick={save} disabled={busy}
          className="ml-auto rounded bg-[var(--color-brio-primary)] px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          {busy ? "Saving…" : `Save version ${next}`}
        </button>
      </div>
      {err && <div className="border-t border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800">{err}</div>}
    </section>
  );
}
