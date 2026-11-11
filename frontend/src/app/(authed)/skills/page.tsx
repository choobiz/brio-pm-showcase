"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  type SkillSummary,
  type SkillVersionRead,
  type VersionPrompt,
  approveVersion,
  getVersionPrompt,
  listSkills,
  rejectVersion,
} from "@/lib/skills";

/**
 * Skills admin — version history, prompt viewer, and the approval gate
 * (versioning Steps 2 + 3).
 *
 * A prompt change deployed in code is auto-registered as a DRAFT candidate
 * (by sync). It does not count as the approved active version until someone
 * approves it here — the one explicit human gate. Approve promotes the draft
 * to active and retires the prior version; both are recorded.
 */
export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<VersionPrompt | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listSkills()
      .then(setSkills)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(refresh, [refresh]);

  const onApprove = async (skillId: string, v: SkillVersionRead) => {
    const note = window.prompt(
      `Approve ${skillId} v${v.version} (${v.fingerprint})?\n\nThis makes it the active prompt and retires the current one.\nOptional note:`,
      "",
    );
    if (note === null) return; // cancelled
    setActing(v.id);
    setError(null);
    try {
      await approveVersion(skillId, v.id, note || null);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setActing(null);
    }
  };

  const onReject = async (skillId: string, v: SkillVersionRead) => {
    const note = window.prompt(
      `Reject ${skillId} v${v.version} (${v.fingerprint})?\nOptional reason:`,
      "",
    );
    if (note === null) return;
    setActing(v.id);
    setError(null);
    try {
      await rejectVersion(skillId, v.id, note || null);
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setActing(null);
    }
  };

  if (error) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        {error}
        <button className="ml-3 underline" onClick={() => setError(null)}>
          dismiss
        </button>
      </div>
    );
  }
  if (skills === null) return <div className="text-sm text-slate-500">Loading…</div>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-800">Skills</h1>
        <p className="mt-1 text-sm text-slate-500 max-w-2xl">
          The AI skill registry. Each skill&rsquo;s prompt lives in code; the
          active version mirrors it (fingerprinted). A deployed prompt change is
          registered as a <strong>draft candidate</strong> and must be{" "}
          <strong>approved here</strong> before it counts as the active version.
        </p>
      </div>

      {skills.length === 0 && (
        <div className="text-sm text-slate-500">No skills registered.</div>
      )}

      {skills.map((s) => {
        const drafts = s.all_versions.filter((v) => v.status === "draft");
        const history = s.all_versions.filter((v) => v.status !== "draft");
        return (
          <Card key={s.skill_id} className="p-5">
            <div className="flex items-start justify-between flex-wrap gap-3">
              <div>
                <div className="text-lg font-semibold text-slate-800">
                  {s.skill_id}
                </div>
                {s.active_version && (
                  <div className="mt-1 flex items-center gap-2 text-xs flex-wrap">
                    <span className="rounded bg-emerald-100 text-emerald-800 px-2 py-0.5 font-medium">
                      active v{s.active_version.version}
                    </span>
                    <span className="font-mono text-slate-500">
                      {s.active_version.fingerprint ?? "—"}
                    </span>
                    <span className="text-slate-400">
                      {s.active_version.prompt_chars.toLocaleString()} chars
                    </span>
                    {s.active_version.approved_by && (
                      <span className="text-slate-400">
                        · approved by {s.active_version.approved_by}
                      </span>
                    )}
                  </div>
                )}
              </div>
              <div className="grid grid-cols-4 gap-4 text-center">
                <Stat label="Runs" value={s.run_count} />
                <Stat label="Edited" value={s.edited_run_count} />
                <Stat label="Signals" value={s.correction_signal_count} />
                <Stat label="Learning" value={s.learning_signal_count} accent />
              </div>
            </div>

            {drafts.length > 0 && (
              <div className="mt-4 rounded-md border border-amber-300 bg-amber-50 p-3">
                <div className="text-[10px] font-semibold uppercase tracking-wider text-amber-700 mb-2">
                  Pending approval ({drafts.length})
                </div>
                <div className="space-y-2">
                  {drafts.map((v) => (
                    <div
                      key={v.id}
                      className="flex items-center justify-between gap-3 text-sm flex-wrap"
                    >
                      <div className="flex items-center gap-3">
                        <span className="font-medium text-slate-700">
                          v{v.version}
                        </span>
                        <span className="font-mono text-xs text-slate-500">
                          {v.fingerprint ?? "—"}
                        </span>
                        <button
                          className="text-xs text-blue-600 underline"
                          onClick={() =>
                            getVersionPrompt(s.skill_id, v.id)
                              .then(setViewing)
                              .catch((e) => setError(String(e)))
                          }
                        >
                          view
                        </button>
                      </div>
                      <div className="flex gap-2">
                        <Button
                          onClick={() => onApprove(s.skill_id, v)}
                          disabled={acting === v.id}
                          className="text-xs h-7"
                        >
                          {acting === v.id ? "…" : "Approve"}
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() => onReject(s.skill_id, v)}
                          disabled={acting === v.id}
                          className="text-xs h-7"
                        >
                          Reject
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-4 border-t border-slate-100 pt-3">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-2">
                Version history
              </div>
              <div className="space-y-1">
                {history.map((v) => (
                  <VersionRow
                    key={v.id}
                    v={v}
                    onView={() =>
                      getVersionPrompt(s.skill_id, v.id)
                        .then(setViewing)
                        .catch((e) => setError(String(e)))
                    }
                  />
                ))}
              </div>
            </div>
          </Card>
        );
      })}

      {viewing && (
        <PromptViewer prompt={viewing} onClose={() => setViewing(null)} />
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent?: boolean;
}) {
  return (
    <div>
      <div
        className={`text-xl font-semibold ${
          accent ? "text-amber-600" : "text-slate-800"
        }`}
      >
        {value.toLocaleString()}
      </div>
      <div className="text-[10px] uppercase tracking-wider text-slate-400">
        {label}
      </div>
    </div>
  );
}

function VersionRow({ v, onView }: { v: SkillVersionRead; onView: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 text-sm py-1">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="font-medium text-slate-700">v{v.version}</span>
        <span
          className={`text-[10px] rounded px-1.5 py-0.5 font-medium ${
            v.status === "active"
              ? "bg-emerald-100 text-emerald-800"
              : v.status === "rejected"
                ? "bg-red-100 text-red-700"
                : "bg-slate-100 text-slate-600"
          }`}
        >
          {v.status}
        </span>
        <span className="font-mono text-xs text-slate-400">
          {v.fingerprint ?? "—"}
        </span>
        {v.approved_by && (
          <span className="text-xs text-slate-400">· {v.approved_by}</span>
        )}
      </div>
      <Button variant="ghost" onClick={onView} className="text-xs h-7">
        View prompt
      </Button>
    </div>
  );
}

function PromptViewer({
  prompt,
  onClose,
}: {
  prompt: VersionPrompt;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="max-h-[85vh] w-full max-w-3xl overflow-hidden bg-white shadow-xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-3 border-b border-slate-200 flex items-center justify-between">
          <div>
            <span className="font-semibold text-slate-800">
              {prompt.skill_id}
            </span>{" "}
            <span className="text-sm text-slate-500">
              v{prompt.version} ({prompt.status})
            </span>
            <span className="ml-2 font-mono text-xs text-slate-400">
              {prompt.fingerprint ?? "—"}
            </span>
          </div>
          <Button variant="ghost" onClick={onClose} className="text-xs h-7">
            Close
          </Button>
        </div>
        <pre className="overflow-auto px-5 py-4 text-xs text-slate-700 whitespace-pre-wrap font-mono">
          {prompt.prompt_template}
        </pre>
      </div>
    </div>
  );
}
