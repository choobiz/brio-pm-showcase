"use client";

import { useEffect, useRef, useState } from "react";

import {
  applyEditPlan,
  getEditPlan,
  requestEdit,
  type EditChange,
  type EditPlan,
  type EditRecordType,
} from "@/lib/aiEdits";
import { streamAIJob, upTo } from "@/lib/aiJobs";
import { previewRateWriteback, type RateDecision, type RateProposal } from "@/lib/rates";
import { RateUpdatesStep } from "@/components/RateUpdatesStep";
import { useJobCost } from "@/lib/useJobCost";

/**
 * Change a record by describing the change, then reading what it will do.
 *
 * The plan is never applied automatically, and the Apply button is not a
 * formality: these records are shown to clients and signed. What the reviewer
 * is deciding on is a list of individual changes, each with the value it
 * replaces, and each can be dropped on its own.
 *
 * The plan is re-checked against the live record when it loads, so a change
 * whose target has moved since the job ran shows as blocked rather than
 * quietly overwriting an edit nobody looked at.
 */

function short(value: unknown, max = 160): string {
  if (value === null || value === undefined) return "—";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/** "detail.line_items[3].description" → "line items › 4 › description" */
function readablePath(path: string): string {
  return path
    .replace(/^detail\./, "")
    .replace(/\[(\d+)\]/g, (_, i) => ` › ${Number(i) + 1}`)
    .replace(/\[\]/g, " › new")
    .split(".")
    .map((p) => p.replace(/_/g, " "))
    .join(" › ");
}

const ACTION_STYLE: Record<string, string> = {
  replace: "bg-sky-100 text-sky-800",
  add: "bg-emerald-100 text-emerald-800",
  remove: "bg-red-100 text-red-800",
};

export default function EditWithPrompt({
  projectId,
  recordType,
  recordId,
  label,
  onApplied,
  asNewVersion = false,
  placeholder,
}: {
  projectId: string;
  recordType: EditRecordType;
  recordId: string;
  /** e.g. "CO-1" — shown so it is obvious which record is being changed. */
  label?: string;
  /** Receives the id of the record that now holds the change: the same one,
   *  or with `asNewVersion` the new version. */
  onApplied: (recordId?: string) => void;
  /** Scopes only: save the result as the next version instead of in place. */
  asNewVersion?: boolean;
  placeholder?: string;
}) {
  const [instruction, setInstruction] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "thinking" | "review" | "applying">("idle");
  const cost = useJobCost(projectId, "ai_edit");
  const [plan, setPlan] = useState<EditPlan | null>(null);
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => () => sourceRef.current?.close(), []);

  const loadPlan = async (id: string) => {
    try {
      const p = await getEditPlan(id);
      setPlan(p);
      // Everything applicable starts ticked. Unticking is the deliberate act,
      // not ticking — the reviewer is approving a proposal, not assembling one.
      setAccepted(new Set(p.changes.filter((c) => c.applicable).map((c) => c.path)));
      setPhase("review");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPhase("idle");
    }
  };

  const submit = async () => {
    if (!instruction.trim() || cost === null) return;
    setErr(null);
    setDone(null);
    setPlan(null);
    setPhase("thinking");
    try {
      const job = await requestEdit(projectId, recordType, recordId, instruction.trim(), cost);
      setJobId(job.id);
      const es = streamAIJob(job.id);
      sourceRef.current = es;
      es.addEventListener("status", (ev) => {
        const payload = JSON.parse((ev as MessageEvent).data) as {
          status: string;
          error: string | null;
        };
        if (payload.status === "done") {
          es.close();
          loadPlan(job.id);
        } else if (payload.status === "error") {
          es.close();
          setErr(payload.error ?? "The edit could not be planned.");
          setPhase("idle");
        }
      });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPhase("idle");
    }
  };

  // Phase 3 save step: on an estimate, a changed price on a linked line also
  // updates the rate library. Ask which rates first; with none, apply directly.
  const [rateStep, setRateStep] = useState<RateProposal[] | null>(null);

  const apply = async () => {
    if (!jobId) return;
    setErr(null);
    if (recordType === "estimate_version") {
      try {
        const { proposals } = await previewRateWriteback({
          ai_edit_job_id: jobId,
          accept_paths: Array.from(accepted),
        });
        if (proposals.length) {
          setRateStep(proposals);
          return;
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
        return;
      }
    }
    await applyWith([]);
  };

  const applyWith = async (rateUpdates: RateDecision[]) => {
    if (!jobId) return;
    setRateStep(null);
    setPhase("applying");
    setErr(null);
    try {
      const res = await applyEditPlan(jobId, Array.from(accepted), asNewVersion, rateUpdates);
      setDone(
        `Applied ${res.applied} change${res.applied === 1 ? "" : "s"}` +
          (res.new_version ? " as a new version" : "") +
          (res.skipped ? `, skipped ${res.skipped}` : "") +
          (res.recomputed?.cost_impact !== undefined
            ? `. Total recomputed to ${res.recomputed.cost_impact.toLocaleString("en-CA", {
                style: "currency",
                currency: "CAD",
              })}.`
            : "."),
      );
      setPlan(null);
      setJobId(null);
      setInstruction("");
      setPhase("idle");
      onApplied(res.record_id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPhase("review");
    }
  };

  const toggle = (path: string) =>
    setAccepted((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const busy = phase === "thinking" || phase === "applying";

  return (
    <>
      {rateStep && (
        <RateUpdatesStep
          proposals={rateStep}
          saveLabel="Apply"
          busy={phase === "applying"}
          onCancel={() => setRateStep(null)}
          onConfirm={applyWith}
        />
      )}
    <div className="mt-3 rounded border border-violet-200 bg-violet-50/40 p-3">
      <div className="text-xs font-medium text-slate-600">
        Change this {recordType.replace(/_/g, " ")}
        {label ? ` (${label})` : ""} by describing it
      </div>

      <div className="mt-1.5 flex flex-wrap gap-2">
        <input
          value={instruction}
          disabled={busy}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) submit();
          }}
          placeholder={placeholder ?? "e.g. drop the GST exclusion and add one for protecting the existing flooring"}
          className="min-w-0 flex-1 basis-48 rounded border border-slate-300 px-2 py-1.5 text-sm"
        />
        <button
          type="button"
          onClick={submit}
          disabled={busy || !instruction.trim() || cost === null}
          className="shrink-0 rounded bg-violet-700 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
        >
          {phase === "thinking" ? "Working…" : cost === null ? "Propose changes" : `Propose changes · ${upTo(cost)}`}
        </button>
      </div>

      {err && (
        <div className="mt-2 rounded border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-700">
          {err}
        </div>
      )}
      {done && (
        <div className="mt-2 rounded border border-emerald-200 bg-emerald-50 px-2 py-1.5 text-xs text-emerald-800">
          {done}
        </div>
      )}
      {phase === "thinking" && (
        <div className="mt-2 text-xs text-slate-500">
          Reading the {recordType.replace(/_/g, " ")} and working out what to change. Nothing is
          saved until you approve it.
        </div>
      )}

      {plan && (
        <div className="mt-3 space-y-2">
          {plan.summary && (
            <div className="text-sm font-medium text-slate-800">{plan.summary}</div>
          )}

          {plan.warning && !plan.locked && (
            <div className="rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
              {plan.warning}
            </div>
          )}

          {plan.locked && (
            <div className="rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
              This {recordType.replace(/_/g, " ")} is <b>{plan.record_status}</b>. It has left the
              building and is the record of what was agreed, so it cannot be edited in place.
              Create a new version instead.
            </div>
          )}

          <ul className="space-y-1.5">
            {plan.changes.map((c: EditChange, i: number) => (
              <li
                key={`${c.path}-${i}`}
                className={`rounded border px-2 py-1.5 ${
                  c.applicable ? "border-slate-200 bg-white" : "border-slate-200 bg-slate-50"
                }`}
              >
                <div className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={accepted.has(c.path)}
                    disabled={!c.applicable || plan.locked || busy}
                    onChange={() => toggle(c.path)}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                          ACTION_STYLE[c.action] ?? "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {c.action}
                      </span>
                      <span className="text-xs font-medium text-slate-700">
                        {readablePath(c.path)}
                      </span>
                    </div>

                    {c.action !== "add" && (
                      <div className="mt-0.5 text-xs text-slate-500 line-through">
                        {short(c.actual ?? c.before)}
                      </div>
                    )}
                    {c.action !== "remove" && (
                      <div className="mt-0.5 text-xs text-slate-800">{short(c.after)}</div>
                    )}
                    {c.why && <div className="mt-0.5 text-[11px] text-slate-500">{c.why}</div>}
                    {!c.applicable && (
                      <div className="mt-1 text-[11px] font-medium text-red-700">
                        Not applied — {c.problem}
                      </div>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>

          {plan.refused.length > 0 && (
            <div className="rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-900">
              <b>Left for you:</b>
              <ul className="mt-0.5 list-disc pl-4">
                {plan.refused.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex items-center gap-3 pt-0.5">
            {/* Nothing proposed: no save button. It used to read "Save as a new
                version with 0 changes" (rollout test, 2026-10-15). */}
            {plan.changes.length > 0 && (
              <button
                type="button"
                onClick={apply}
                disabled={busy || plan.locked || accepted.size === 0}
                className="rounded bg-violet-700 px-3 py-1 text-xs font-medium text-white disabled:opacity-60"
              >
                {phase === "applying"
                  ? "Applying…"
                  : `${asNewVersion ? "Save as a new version with" : "Apply"} ${accepted.size} change${accepted.size === 1 ? "" : "s"}`}
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setPlan(null);
                setJobId(null);
              }}
              disabled={busy}
              className="text-xs text-slate-500 hover:text-slate-700"
            >
              {plan.changes.length > 0 ? "Discard" : "Close"}
            </button>
            {plan.blocked_count > 0 && (
              <span className="text-[11px] text-slate-500">
                {plan.blocked_count} cannot be applied
              </span>
            )}
          </div>
        </div>
      )}
    </div>
    </>
  );
}
