"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  type DiscussionPoint,
  type ProjectRead,
  suggestDiscussionPoints,
  updateProject,
} from "@/lib/projects";

/**
 * Per-project "What We Discussed" editor. These points drive the client
 * package's Discussion Summary (projects.discussion_points). Manual add/edit/
 * remove + an AI "Suggest from project" draft that the user reviews before
 * saving. A point with computed==="stormwater" keeps its {sw}/{site}/{total}
 * tokens (filled live at render) — shown read-only-ish with a hint.
 */
export function DiscussionPointsCard({
  project,
  onSaved,
}: {
  project: ProjectRead;
  onSaved: (p: ProjectRead) => void;
}) {
  const [points, setPoints] = useState<DiscussionPoint[]>(
    project.discussion_points ?? [],
  );
  const [busy, setBusy] = useState<null | "save" | "ai">(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const dirty =
    JSON.stringify(points) !==
    JSON.stringify(project.discussion_points ?? []);

  function edit(i: number, field: "title" | "body", value: string) {
    setPoints((ps) => ps.map((p, j) => (j === i ? { ...p, [field]: value } : p)));
  }
  const add = () => setPoints((ps) => [...ps, { title: "", body: "" }]);
  const remove = (i: number) =>
    setPoints((ps) => ps.filter((_, j) => j !== i));
  const move = (i: number, d: -1 | 1) =>
    setPoints((ps) => {
      const j = i + d;
      if (j < 0 || j >= ps.length) return ps;
      const next = [...ps];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });

  async function save() {
    setBusy("save");
    setError(null);
    setNote(null);
    try {
      const cleaned = points.filter((p) => p.title.trim() || p.body.trim());
      const updated = await updateProject(project.id, {
        discussion_points: cleaned,
      });
      setPoints(updated.discussion_points ?? cleaned);
      onSaved(updated);
      setNote("Saved.");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function suggest() {
    setBusy("ai");
    setError(null);
    setNote(null);
    try {
      const { points: drafts } = await suggestDiscussionPoints(project.id);
      if (!drafts.length) {
        setNote("Not enough project context yet to draft points.");
      } else {
        setPoints((ps) => [...ps, ...drafts]);
        setNote(
          `Added ${drafts.length} AI draft${drafts.length > 1 ? "s" : ""} — review, edit, then Save.`,
        );
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Client Discussion Points
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            Drives the client package&rsquo;s &ldquo;What We Discussed&rdquo;
            section. Specific to this project.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            disabled={busy !== null}
            onClick={suggest}
            title="Draft points from this project's index, scope, and estimate"
          >
            {busy === "ai" ? "Drafting…" : "✨ Suggest from project"}
          </Button>
          <Button disabled={busy !== null || !dirty} onClick={save}>
            {busy === "save" ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>

      {error && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}
      {note && (
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
          {note}
        </div>
      )}

      <div className="mt-3 space-y-3">
        {points.length === 0 && (
          <div className="text-sm text-slate-400">
            No discussion points yet. Add one, or use &ldquo;Suggest from
            project&rdquo;.
          </div>
        )}
        {points.map((p, i) => (
          <div key={i} className="rounded-md border border-slate-200 p-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-400">
                {i + 1}
              </span>
              <Input
                value={p.title}
                placeholder="Title (e.g. Exterior finish — options reviewed)"
                onChange={(e) => edit(i, "title", e.target.value)}
                className="flex-1"
              />
              <button
                onClick={() => move(i, -1)}
                disabled={i === 0}
                className="px-1 text-slate-400 hover:text-slate-700 disabled:opacity-30"
                title="Move up"
              >
                ↑
              </button>
              <button
                onClick={() => move(i, 1)}
                disabled={i === points.length - 1}
                className="px-1 text-slate-400 hover:text-slate-700 disabled:opacity-30"
                title="Move down"
              >
                ↓
              </button>
              <button
                onClick={() => remove(i)}
                className="px-1 text-slate-400 hover:text-red-600"
                title="Remove"
              >
                ✕
              </button>
            </div>
            {p.computed === "stormwater" && (
              <div className="mt-1 text-[11px] text-amber-700">
                Stormwater point — {"{sw}/{site}/{total}"} fill in live from the
                active estimate.
              </div>
            )}
            <textarea
              value={p.body}
              placeholder="What was discussed…"
              onChange={(e) => edit(i, "body", e.target.value)}
              rows={3}
              className="mt-2 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
            />
          </div>
        ))}
      </div>

      <div className="mt-3">
        <Button variant="secondary" disabled={busy !== null} onClick={add}>
          + Add point
        </Button>
      </div>
    </Card>
  );
}
