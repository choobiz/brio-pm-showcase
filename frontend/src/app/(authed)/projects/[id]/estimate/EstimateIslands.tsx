"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import EditWithPrompt from "@/components/EditWithPrompt";
import { WorkspaceError } from "@/components/WorkspaceError";
import { answerFinish, getFinish, setDelivery, suggestFinishes, type DeliveredAs, type FinishState } from "@/lib/finish";
import { discardDraft } from "@/lib/workspaceDrafts";

const DELIVERED: { value: DeliveredAs; label: string }[] = [
  { value: "finished", label: "Finished" },
  { value: "shell", label: "Shell" },
  { value: "part_shell", label: "Part shell" },
];

/**
 * What to settle before the next version: delivered-as first, then — for a
 * finished or part-finished space only — each finish, proposed and confirmed.
 * Only confirmed answers reach the estimate as fact; the rest are priced as a
 * typical choice and listed as assumptions. A shell has no finishes.
 */
export function BeforeNextVersion({ projectId, divisionSet }: { projectId: string; divisionSet: string | null }) {
  const [state, setState] = useState<FinishState | null>(null);
  const [note, setNote] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const take = (s: FinishState) => {
    setState(s);
    setNote(s.part_shell_note ?? "");
    setDrafts(Object.fromEntries(s.questions.map((q) => [q.key, q.answer ?? ""])));
  };
  useEffect(() => {
    getFinish(projectId).then(take).catch(setErr);
  }, [projectId]);

  const run = async (what: string, fn: () => Promise<FinishState>) => {
    setBusy(what);
    setErr(null);
    try {
      take(await fn());
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  if (!state) return err != null ? <WorkspaceError error={err} /> : <p className="m-0 text-sm text-slate-500">Loading…</p>;

  return (
    <div id="finishes" className="space-y-3">
      <div className="grid gap-3 md:grid-cols-3">
        <label className="flex flex-col gap-1 text-[13px] font-semibold">
          Delivered as
          <select
            value={state.delivered_as ?? ""}
            disabled={busy !== null}
            onChange={(e) => run("delivery", () => setDelivery(projectId, { delivered_as: (e.target.value || null) as DeliveredAs | null }))}
            className="rounded-md border border-slate-400 px-2 py-2 text-sm font-normal"
          >
            <option value="">Not set</option>
            {DELIVERED.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <div className="flex flex-col gap-1 text-[13px] font-semibold">
          Cost breakdown
          <div className="rounded-md border border-slate-200 bg-slate-50 px-2 py-2 text-sm font-normal text-slate-700">
            {divisionSet ?? "None: set the project type on Overview"}
          </div>
        </div>
      </div>

      {state.delivered_as === null && (
        <p className="m-0 text-[13px] text-slate-600">
          Set this first. A shell has no finishes to choose; a finished space is asked about each one.
        </p>
      )}

      {state.delivered_as === "shell" && (
        <p className="m-0 text-[13px] text-slate-700">
          No finishes to choose. Not included: {state.shell_excluded.join(", ")}. Insulation and doors are priced.
        </p>
      )}

      {state.delivered_as === "part_shell" && (
        <label className="block text-[13px] font-semibold">
          Which areas are left unfinished?
          <textarea
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onBlur={() => note !== (state.part_shell_note ?? "") && run("note", () => setDelivery(projectId, { part_shell_note: note }))}
            placeholder="e.g. Basement suite left as a shell; main floor finished."
            className="mt-1 w-full rounded-md border border-slate-400 px-2 py-1.5 text-sm font-normal"
          />
        </label>
      )}

      {state.asks_finishes && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-[13px] font-semibold">
              Finishes · {state.confirmed} of {state.total} confirmed
            </div>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => run("suggest", () => suggestFinishes(projectId))}
              className="rounded-md border border-slate-900 px-3 py-1.5 text-sm disabled:opacity-50"
            >
              {busy === "suggest" ? "Reading the scope…" : "Suggest answers from the scope"}
            </button>
          </div>
          <ul className="m-0 list-none space-y-2 p-0">
            {state.questions.map((q) => {
              const draft = drafts[q.key] ?? "";
              const changed = draft !== (q.answer ?? "");
              return (
                <li key={q.key} className={`rounded-md border p-3 text-[13px] ${q.confirmed ? "border-emerald-300" : "border-slate-300"}`}>
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <b>{q.label}</b>
                    <span className={q.confirmed ? "text-emerald-800" : "text-slate-500"}>
                      {q.confirmed ? "Confirmed" : "Not confirmed: priced as an assumption"}
                    </span>
                  </div>
                  <div className="text-slate-600">{q.question}</div>
                  <div className="mt-1.5 flex flex-wrap gap-2">
                    <input
                      value={draft}
                      onChange={(e) => setDrafts((d) => ({ ...d, [q.key]: e.target.value }))}
                      aria-label={q.label}
                      placeholder="Your answer"
                      className="min-w-0 flex-1 rounded-md border border-slate-400 px-2 py-1.5 text-sm"
                    />
                    <button
                      type="button"
                      disabled={busy !== null || !draft.trim() || (q.confirmed && !changed)}
                      onClick={() => run(q.key, () => answerFinish(projectId, q.key, draft, true))}
                      className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-40"
                    >
                      {q.confirmed && !changed ? "Confirmed" : "Confirm"}
                    </button>
                  </div>
                  {(q.suggested || q.why) && (
                    <div className="mt-1 text-xs text-slate-500">
                      {q.suggested ? `Suggested: ${q.suggested}${q.why ? ` (${q.why})` : ""}` : `No suggestion: ${q.why}`}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          <p className="m-0 text-xs text-slate-500">
            Suggestions are read from the latest scope and are not decisions. Only confirmed answers reach the estimate; the
            rest are priced as a typical choice and listed on it as assumptions.
          </p>
        </div>
      )}
      {err != null && <WorkspaceError error={err} />}
    </div>
  );
}

/** Discard an AI draft that was never kept. Refused server-side if anything uses it. */
export function DiscardDraftButton({ versionId, label }: { versionId: string; label: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  return (
    <span className="inline-flex flex-col">
      <button
        type="button"
        disabled={busy}
        onClick={async () => {
          if (!confirm(`Discard ${label}? It is kept in the history and can be restored by an admin.`)) return;
          setBusy(true);
          setErr(null);
          try {
            await discardDraft("estimate_version", versionId, "discarded from the Estimate page");
            router.refresh();
          } catch (e) {
            setErr(e);
          } finally {
            setBusy(false);
          }
        }}
        className="rounded border border-slate-400 px-2 py-1 text-xs disabled:opacity-50"
      >
        {busy ? "Discarding…" : "Discard"}
      </button>
      {err != null && <WorkspaceError error={err} className="mt-1" />}
    </span>
  );
}

/** Describe a change to a version; the accepted result is saved as a new draft version. */
export function ChangeByDescribing({
  projectId,
  versionId,
  label,
  nextVersion,
}: {
  projectId: string;
  versionId: string;
  label: string;
  nextVersion: number;
}) {
  const router = useRouter();
  return (
    <div>
      <EditWithPrompt
        projectId={projectId}
        recordType="estimate_version"
        recordId={versionId}
        label={label}
        asNewVersion
        placeholder="e.g. Swap baseboard heaters for a ducted heat pump"
        onApplied={(newId) => {
          if (newId && newId !== versionId) router.push(`/projects/${projectId}/estimate/${newId}`);
          else router.refresh();
        }}
      />
      <p className="m-0 mt-1 text-xs text-slate-500">
        You approve each change. Accepting saves v{nextVersion} as a draft; {label} stays as it was until you keep the
        new one.
      </p>
    </div>
  );
}
