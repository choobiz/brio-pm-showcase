"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  type FeedbackCategory,
  TAG_OPTIONS,
  getRunByArtifact,
  postCorrections,
} from "@/lib/skills";

/**
 * Post-save feedback modal.
 *
 * Opens after a successful save when the editor wants to capture
 * correction signal for the originating AI run. The modal:
 *  1. Looks up the skill_run_id via /skill-runs/by-artifact
 *  2. Asks the user to tag the edit with one of 8 categories (or Skip)
 *  3. POSTs to /skill-runs/{id}/corrections with the chosen tag +
 *     accepted_output → backend computes the diff + writes signals
 *
 * Tag taxonomy (`ai_miss_*` vs `project_*` vs `other`) is in
 * lib/skills.ts and must match the backend's KNOWN_CATEGORIES.
 *
 * Modal is silent (returns null, no UI) when no AI run is linked
 * to the artifact — manually-authored scopes/estimates won't ever
 * see the popup.
 */
export function SkillFeedbackModal({
  skillId,
  artifactMarker,
  artifactValue,
  acceptedOutput,
  onClose,
}: {
  skillId: string;
  artifactMarker: string;
  artifactValue: string;
  acceptedOutput: Record<string, unknown>;
  onClose: () => void;
}) {
  const [runId, setRunId] = useState<string | null | undefined>(undefined); // undefined = loading, null = no run found
  const [selected, setSelected] = useState<FeedbackCategory | null>(null);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submittedSignals, setSubmittedSignals] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await getRunByArtifact(
          skillId,
          artifactMarker,
          artifactValue,
        );
        if (!cancelled) setRunId(res?.skill_run_id ?? null);
      } catch (e) {
        if (!cancelled) {
          setError(String(e));
          setRunId(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [skillId, artifactMarker, artifactValue]);

  // No linked run → silently close (the editor save still succeeded;
  // there's just nothing to tag).
  useEffect(() => {
    if (runId === null) {
      onClose();
    }
  }, [runId, onClose]);

  const submit = async (category: FeedbackCategory | null) => {
    if (!runId) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await postCorrections(
        runId,
        acceptedOutput,
        category,
        note.trim() || null,
      );
      setSubmittedSignals(res.signal_count);
      // small delay so the user sees the "captured" confirmation
      setTimeout(onClose, 900);
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  if (runId === undefined) return null; // still resolving — no flicker
  if (runId === null) return null;

  const grouped = {
    ai_miss: TAG_OPTIONS.filter((o) => o.group === "ai_miss"),
    project_specific: TAG_OPTIONS.filter((o) => o.group === "project_specific"),
    other: TAG_OPTIONS.filter((o) => o.group === "other"),
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={() => !submitting && onClose()}
    >
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-slate-200">
          <h2 className="text-lg font-semibold text-slate-800">
            Why did you make this edit?
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            Your answer helps BRIO&rsquo;s AI learn what it gets right and
            what to improve. Only <strong>AI mistakes</strong> feed the
            auto-improvement loop &mdash; project-specific judgement is
            recorded but doesn&rsquo;t change the model.
          </p>
        </div>

        <div className="px-5 py-4 space-y-5">
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-red-700 mb-2">
              AI mistake (feeds learning)
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {grouped.ai_miss.map((o) => (
                <TagButton
                  key={o.value}
                  selected={selected === o.value}
                  onClick={() => setSelected(o.value)}
                  label={o.label}
                  hint={o.hint}
                  variant="ai_miss"
                />
              ))}
            </div>
          </div>
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-2">
              Project-specific (recorded, not learned)
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {grouped.project_specific.map((o) => (
                <TagButton
                  key={o.value}
                  selected={selected === o.value}
                  onClick={() => setSelected(o.value)}
                  label={o.label}
                  hint={o.hint}
                  variant="project_specific"
                />
              ))}
              {grouped.other.map((o) => (
                <TagButton
                  key={o.value}
                  selected={selected === o.value}
                  onClick={() => setSelected(o.value)}
                  label={o.label}
                  hint={o.hint}
                  variant="other"
                />
              ))}
            </div>
          </div>
          {selected === "other" && (
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">
                Note (optional)
              </label>
              <textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={3}
                className="w-full border border-slate-300 rounded p-2 text-sm"
                placeholder="Describe in a sentence."
              />
            </div>
          )}
          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 break-words">
              {error}
            </div>
          )}
          {submittedSignals != null && (
            <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
              Captured {submittedSignals}{" "}
              {submittedSignals === 1 ? "signal" : "signals"}. Thanks.
            </div>
          )}
        </div>

        <div className="flex justify-between items-center gap-2 px-5 py-3 border-t border-slate-200 bg-slate-50">
          <Button
            variant="ghost"
            onClick={() => submit(null)}
            disabled={submitting}
            title="Close without tagging. Diff is still recorded but untagged."
          >
            Skip
          </Button>
          <Button
            onClick={() => selected && submit(selected)}
            disabled={!selected || submitting || submittedSignals != null}
          >
            {submitting ? "Saving…" : "Submit"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function TagButton({
  selected,
  onClick,
  label,
  hint,
  variant,
}: {
  selected: boolean;
  onClick: () => void;
  label: string;
  hint: string;
  variant: "ai_miss" | "project_specific" | "other";
}) {
  const palette: Record<typeof variant, { bg: string; border: string }> = {
    ai_miss: {
      bg: selected ? "bg-red-100" : "hover:bg-red-50 bg-white",
      border: selected ? "border-red-400 ring-2 ring-red-200" : "border-slate-300",
    },
    project_specific: {
      bg: selected ? "bg-blue-100" : "hover:bg-blue-50 bg-white",
      border: selected ? "border-blue-400 ring-2 ring-blue-200" : "border-slate-300",
    },
    other: {
      bg: selected ? "bg-slate-200" : "hover:bg-slate-100 bg-white",
      border: selected ? "border-slate-500 ring-2 ring-slate-300" : "border-slate-300",
    },
  };
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-left p-2 rounded border ${palette[variant].bg} ${palette[variant].border} transition`}
    >
      <div className="text-sm font-semibold text-slate-800">{label}</div>
      <div className="text-[11px] text-slate-500 mt-0.5 leading-snug">{hint}</div>
    </button>
  );
}
