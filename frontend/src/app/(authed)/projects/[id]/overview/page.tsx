"use client";

import { errorMessage } from "@/lib/workspaceApi";
import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { Box, Label, Tag } from "@/components/kit";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DiscussionPointsCard } from "@/components/DiscussionPointsCard";
import { WorkspaceError } from "@/components/WorkspaceError";
import { fmtMoney } from "@/lib/fmt";
import { getRoad, type Road } from "@/lib/home";
import {
  DELIVERED_AS_LABELS,
  FINISH_LEVEL_LABELS,
  PROJECT_STATUSES,
  PROJECT_TYPES,
  PROJECT_TYPE_LABELS,
  STATUS_LABELS,
  getProject,
  notifyClient,
  updateProject,
  type DeliveredAs,
  type FinishLevel,
  type ProjectRead,
  type ProjectStatus,
} from "@/lib/projects";
import { getInterview, type Interview, type InterviewQuestion } from "@/lib/workspaceInterview";

// ⚠️ NOT imported from @/lib/workbench — that reaches @/lib/api, which uses
// next/headers and is server-only. This page is a client component, so it goes
// through the same /api/backend proxy every other card here uses.
type NoteSummary = {
  id: string; title: string; version_number: number; is_superseded: boolean;
};
import { PackageStatusBanner } from "../workbench/estimates/[vid]/PackageStatusBanner";
import { GettingStartedGuide } from "./GettingStartedGuide";

type Patch = Parameters<typeof updateProject>[1];

/**
 * Project overview (wireframe round 1, Overview board): how far this job is
 * from an estimate, the one thing to do next, a snapshot, and the project's
 * details, all editable here. Everything that was on the old overview is kept
 * below under "More about this project"; nothing was removed.
 */
export default function Overview({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [project, setProject] = useState<ProjectRead | null>(null);
  const [road, setRoad] = useState<Road | null>(null);
  // undefined = still loading, null = the check failed. Neither may read as
  // "nothing outstanding": that claim needs an answer from the server.
  const [interview, setInterview] = useState<Interview | null | undefined>(undefined);
  const [notes, setNotes] = useState<NoteSummary[]>([]);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setError(null);
    getProject(id).then(setProject).catch(setError);
    getRoad(id).then(setRoad).catch(setError);
    // The interview is advisory: if it fails the page still works.
    getInterview(id).then(setInterview).catch(() => setInterview(null));
  }, [id]);

  useEffect(() => {
    load();
    fetch(`/api/backend/projects/${id}/workbench/narratives`)
      .then((r) => (r.ok ? r.json() : []))
      .then((ns: NoteSummary[]) => setNotes(ns.filter((n) => !n.is_superseded)))
      .catch(() => {});
  }, [id, load]);

  /** Every write on this page goes through here: PATCH the project, then
   *  re-read the road and the interview, because an answer changes both. */
  const save = useCallback(
    async (patch: Patch) => {
      const next = await updateProject(id, patch);
      setProject(next);
      getRoad(id).then(setRoad).catch(() => {});
      getInterview(id).then(setInterview).catch(() => {});
      return next;
    },
    [id],
  );

  if (error) return <WorkspaceError error={error} onRetry={load} />;
  if (!project || !road) return <div className="text-sm text-slate-500">Loading project…</div>;

  return (
    <div className="max-w-6xl space-y-5">
      <PackageStatusBanner projectId={id} />

      <RoadStrip road={road} />

      <NextStep projectId={id} project={project} road={road} interview={interview} onSave={save} />

      {road.current === "documents" && <GettingStartedGuide projectId={id} />}

      <Snapshot projectId={id} road={road} />

      <ProjectDetails project={project} onSave={save} />

      <details className="group rounded-lg border border-slate-300 bg-white">
        <summary className="cursor-pointer list-none px-5 py-4 text-xs font-semibold uppercase tracking-wider text-slate-600">
          More about this project <span className="font-normal normal-case tracking-normal text-slate-500">· summary, notes, client update, warranty, indexing, discussion points</span>
        </summary>
        <div className="space-y-4 border-t border-slate-200 p-4">
          <SummaryCard project={project} onSaved={setProject} />
          <NotifyClient projectId={id} />
          <NotesCard projectId={id} notes={notes} />
          <QuantityLinks projectId={id} />
          <ToggleCard
            title="New Home — 2-5-10 Warranty"
            on={project.home_warranty_required}
            label="2-5-10 warranty"
            onToggle={() => save({ home_warranty_required: !project.home_warranty_required })}
          >
            Turn ON for a <b>new home / dwelling</b> (house, laneway house, secondary suite) — the
            contract then discloses the mandatory BC 2-5-10 home warranty (Travelers). Leave OFF for
            accessory buildings (garage, shop, office).
            {!project.home_warranty_required && looksLikeDwelling(project) && (
              <div className="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800">
                This looks like a dwelling — should 2-5-10 apply?
              </div>
            )}
          </ToggleCard>
          <ToggleCard
            title="AI Document Indexing"
            on={project.index_documents}
            label="Auto-indexing"
            onToggle={() => save({ index_documents: !project.index_documents })}
          >
            When ON, every uploaded document is auto-summarised by the AI and folded into the Project
            Index. Turn this OFF for archive or historical-data projects so the indexer doesn&rsquo;t
            burn tokens on every upload.
          </ToggleCard>
          <DiscussionPointsCard project={project} onSaved={setProject} />
        </div>
      </details>
    </div>
  );
}

// ── Road to an estimate ─────────────────────────────────────────────────────

function RoadStrip({ road }: { road: Road }) {
  return (
    <Box className="p-4">
      <Label className="mb-3">Road to an estimate</Label>
      <ol className="m-0 grid list-none grid-cols-2 gap-2 p-0 sm:grid-cols-3 lg:grid-cols-6">
        {road.steps.map((s, n) => (
          <li key={s.key}>
            <Link
              href={s.href}
              aria-current={s.current ? "step" : undefined}
              className={`flex h-full flex-col gap-1 rounded-md border p-3 no-underline ${
                s.current
                  ? "border-2 border-slate-900 bg-white"
                  : s.state === "done"
                    ? "border-green-700/40 bg-green-50"
                    : "border-slate-300 bg-white"
              }`}
            >
              <span className="text-xs text-slate-500">
                {n + 1} ·{" "}
                {s.state === "done" ? (
                  <span className="text-green-800">Done</span>
                ) : s.state === "not_needed" ? (
                  "Not needed"
                ) : s.current ? (
                  <b className="text-slate-900">Next</b>
                ) : (
                  "To do"
                )}
              </span>
              <b className="text-sm text-slate-900">{s.label}</b>
              <span className="text-xs text-slate-600">{s.detail}</span>
            </Link>
          </li>
        ))}
      </ol>
    </Box>
  );
}

// ── Next step: answer the outstanding questions in place ────────────────────

function NextStep({
  projectId,
  project,
  road,
  interview,
  onSave,
}: {
  projectId: string;
  project: ProjectRead;
  road: Road;
  interview: Interview | null | undefined;
  onSave: (p: Patch) => Promise<ProjectRead>;
}) {
  const questions = interview ? [...interview.blocking, ...interview.material] : [];
  const [skipped, setSkipped] = useState<string[]>([]);
  const open = questions.filter((q) => !skipped.includes(q.id));
  const q = open[0];
  const step = road.steps.find((s) => s.current);

  if (interview === undefined) {
    return (
      <Box className="border-2 border-slate-900">
        <Label className="mb-2">Next step</Label>
        <p className="m-0 text-sm text-slate-500">Checking what is still open…</p>
      </Box>
    );
  }

  if (!q) {
    return (
      <Box className="border-2 border-slate-900">
        <Label className="mb-2">Next step</Label>
        {step ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-lg font-semibold text-slate-900">{step.label}</div>
              <div className="text-sm text-slate-600">{step.detail}</div>
            </div>
            <Link href={step.href} className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white no-underline">
              Go →
            </Link>
          </div>
        ) : interview === null ? (
          <p className="m-0 text-sm text-slate-700">
            Could not check the open questions just now. Reload the page to try again.
          </p>
        ) : (
          <p className="m-0 text-sm text-slate-700">
            Nothing outstanding. Every question this can check is answered.
            {skipped.length > 0 && (
              <button type="button" onClick={() => setSkipped([])} className="ml-2 text-blue-700 hover:underline">
                Show the {skipped.length} skipped
              </button>
            )}
          </p>
        )}
      </Box>
    );
  }

  return (
    <Box className="border-2 border-slate-900">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <Label>
          Next step · {questions.length - open.length + 1} of {questions.length}
        </Label>
        {q.blocking ? <Tag tone="warn">Blocks the estimate</Tag> : <Tag>Changes the number</Tag>}
      </div>
      <div className="text-lg font-semibold text-slate-900">{q.question}</div>
      <p className="m-0 mt-1 max-w-3xl text-sm text-slate-600">{q.why}</p>
      <div className="mt-3">
        <Answer key={q.id} projectId={projectId} project={project} q={q} onSave={onSave} />
      </div>
      {!q.blocking && (
        <button
          type="button"
          onClick={() => setSkipped((s) => [...s, q.id])}
          className="mt-3 text-sm text-slate-500 hover:text-slate-800"
        >
          Skip for now
        </button>
      )}
    </Box>
  );
}

function Answer({
  projectId,
  project,
  q,
  onSave,
}: {
  projectId: string;
  project: ProjectRead;
  q: InterviewQuestion;
  onSave: (p: Patch) => Promise<ProjectRead>;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [year, setYear] = useState("");
  const a = q.answer;

  const write = async (patch: Patch) => {
    setBusy(true);
    setErr(null);
    try {
      await onSave(patch);
    } catch (e) {
      setErr(errorMessage(e));
      setBusy(false);
    }
  };

  // Answered somewhere else entirely (reviewing quantities, signing off).
  if (!a) {
    return q.action ? (
      <Link href={q.action} className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white no-underline">
        Go and do it →
      </Link>
    ) : null;
  }

  // A year: free entry.
  if (a.field === "building_age") {
    return (
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (year) write({ building_age: Number(year) });
        }}
      >
        <input
          aria-label="Year built"
          inputMode="numeric"
          placeholder="e.g. 1962"
          value={year}
          onChange={(e) => setYear(e.target.value.replace(/[^0-9]/g, "").slice(0, 4))}
          className="w-28 rounded-md border border-slate-400 px-3 py-2 text-sm"
        />
        <button type="submit" disabled={busy || year.length !== 4} className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-40">
          Save
        </button>
        {err && <span className="text-sm text-red-700">{err}</span>}
      </form>
    );
  }

  // One of a fixed set of options.
  return (
    <div className="flex flex-col gap-2">
      <div className="grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(220px,1fr))]">
        {(q.options ?? []).map((label, i) => {
          const value = a.values?.[i];
          if (value === null) {
            // "Upload them": done on the Documents tab, not by writing a value.
            return (
              <Link
                key={label}
                href={`/projects/${projectId}/documents`}
                className="rounded-md border border-slate-400 px-3 py-2.5 text-sm text-slate-900 no-underline hover:border-slate-900"
              >
                {label} →
              </Link>
            );
          }
          const patch: Patch =
            a.field === "drawing_decisions"
              ? { drawing_decisions: { ...project.drawing_decisions, [a.key as string]: value as "allowance" | "not_in_scope" } }
              : ({ [a.field]: value } as Patch);
          return (
            <button
              key={label}
              type="button"
              disabled={busy}
              onClick={() => write(patch)}
              className="rounded-md border border-slate-400 bg-white px-3 py-2.5 text-left text-sm text-slate-900 hover:border-slate-900 disabled:opacity-50"
            >
              {label}
            </button>
          );
        })}
      </div>
      {err && <span className="text-sm text-red-700">{err}</span>}
    </div>
  );
}

// ── Snapshot ────────────────────────────────────────────────────────────────

function Snapshot({ projectId, road }: { projectId: string; road: Road }) {
  const s = road.snapshot;
  const base = `/projects/${projectId}`;
  const cards: { label: string; value: string; sub: string; href: string }[] = [
    { label: "Documents", value: String(s.documents), sub: "files uploaded", href: `${base}/documents` },
    {
      label: "Scope",
      value: s.scope_versions ? `${s.scope_versions}` : "None",
      sub: s.scope_versions === 1 ? "version" : s.scope_versions ? "versions" : "not written yet",
      href: `${base}/scope`,
    },
    {
      label: "Estimate",
      value: s.estimate ? fmtMoney(s.estimate.total_with_gst) : "None",
      sub: s.estimate
        ? `v${s.estimate.version_number} with GST · ${s.estimate_versions} version${s.estimate_versions !== 1 ? "s" : ""}`
        : s.estimate_versions
          ? `${s.estimate_versions} draft${s.estimate_versions !== 1 ? "s" : ""}, none active`
          : "not made yet",
      href: `${base}/estimate`,
    },
    {
      label: "Money",
      value: s.contract ? fmtMoney(s.contract.amount) : "—",
      sub: s.contract
        ? `${s.contract.source === "contract" ? "contract" : `from estimate v${s.contract.version_number}`} + GST · ${fmtMoney(s.invoices.paid)} paid of ${fmtMoney(s.invoices.total)} invoiced`
        : `${s.invoices.count} invoice${s.invoices.count !== 1 ? "s" : ""}`,
      href: `${base}/money`,
    },
  ];
  return (
    <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(220px,1fr))]">
      {cards.map((c) => (
        <Link key={c.label} href={c.href} className="rounded-lg border border-slate-300 bg-white p-4 no-underline hover:border-slate-500">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-600">{c.label}</div>
          <div className="mt-1 text-xl font-bold text-slate-900">{c.value}</div>
          <div className="text-xs text-slate-600">{c.sub}</div>
        </Link>
      ))}
    </div>
  );
}

// ── Project details, editable in one place ──────────────────────────────────

function ProjectDetails({ project, onSave }: { project: ProjectRead; onSave: (p: Patch) => Promise<ProjectRead> }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const start = () => {
    setDraft({
      name: project.name,
      address: project.address ?? "",
      type: project.type ?? "",
      status: project.status,
      building_age: project.building_age?.toString() ?? "",
      delivered_as: project.delivered_as ?? "",
      sqft: project.sqft?.toString() ?? "",
      building_type: project.building_type ?? "",
      google_drive_url: project.google_drive_url ?? "",
    });
    setErr(null);
    setEditing(true);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.name.trim()) return setErr("The project needs a name.");
    const text = (k: string) => draft[k].trim() || null;
    const num = (k: string) => (draft[k].trim() ? Number(draft[k]) : null);
    const patch: Patch = {
      name: draft.name.trim(),
      address: text("address"),
      building_age: num("building_age"),
      sqft: num("sqft"),
      building_type: text("building_type"),
      google_drive_url: text("google_drive_url"),
      delivered_as: (text("delivered_as") as DeliveredAs | null),
      status: draft.status as ProjectStatus,
    };
    // Type cannot be cleared once set (it picks the cost breakdown), only changed.
    if (draft.type) patch.type = draft.type;
    setBusy(true);
    setErr(null);
    try {
      await onSave(patch);
      setEditing(false);
    } catch (e2) {
      setErr(errorMessage(e2));
    } finally {
      setBusy(false);
    }
  };

  const typeLabel = project.type ? PROJECT_TYPE_LABELS[project.type as keyof typeof PROJECT_TYPE_LABELS] ?? project.type : null;
  const rows: [string, React.ReactNode][] = [
    ["Address", project.address],
    ["Type", typeLabel],
    ["Status", STATUS_LABELS[project.status as ProjectStatus] ?? project.status],
    ["Year built", project.building_age],
    [
      "Finishes",
      project.delivered_as === "shell" ? (
        "None: delivered as a shell"
      ) : (
        <Link href={`/projects/${project.id}/estimate#finishes`} className="text-blue-700">
          Asked on the Estimate page
        </Link>
      ),
    ],
    ["Delivered as", project.delivered_as ? DELIVERED_AS_LABELS[project.delivered_as] : null],
    ["Approx. sq ft", project.sqft?.toLocaleString()],
    ["Building type", project.building_type],
    [
      "Drive folder",
      project.google_drive_url ? (
        <a href={project.google_drive_url} target="_blank" rel="noopener noreferrer" className="text-blue-700">
          Open ↗
        </a>
      ) : null,
    ],
  ];

  const field = (k: string, label: string, el: React.ReactNode) => (
    <label key={k} className="flex flex-col gap-1 text-[13px] font-semibold text-slate-700">
      {label}
      {el}
    </label>
  );
  const input = (k: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <input
      value={draft[k]}
      onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
      className="rounded-md border border-slate-400 px-2.5 py-2 text-sm font-normal"
      {...props}
    />
  );
  const select = (k: string, options: [string, string][], blank?: string) => (
    <select
      value={draft[k]}
      onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))}
      className="rounded-md border border-slate-400 px-2.5 py-2 text-sm font-normal"
    >
      {blank !== undefined && <option value="">{blank}</option>}
      {options.map(([v, l]) => (
        <option key={v} value={v}>
          {l}
        </option>
      ))}
    </select>
  );

  return (
    <Box>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <Label>Project details</Label>
        {!editing && (
          <button type="button" onClick={start} className="text-sm text-blue-700 hover:text-blue-900">
            Edit
          </button>
        )}
      </div>
      {editing ? (
        <form onSubmit={submit} className="space-y-3">
          <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(240px,1fr))]">
            {field("name", "Name", input("name"))}
            {field("address", "Address", input("address"))}
            {field(
              "type",
              "Type",
              select("type", PROJECT_TYPES.map((t) => [t, PROJECT_TYPE_LABELS[t]]), project.type ? undefined : "Not set"),
            )}
            {field(
              "status",
              "Status",
              select("status", PROJECT_STATUSES.filter((s) => s !== "archived").map((s) => [s, STATUS_LABELS[s]])),
            )}
            {field("building_age", "Year built", input("building_age", { inputMode: "numeric", placeholder: "e.g. 1962" }))}
            {field(
              "delivered_as",
              "Delivered as",
              select("delivered_as", Object.entries(DELIVERED_AS_LABELS), "Not set"),
            )}
            {field("sqft", "Approx. sq ft", input("sqft", { inputMode: "numeric" }))}
            {field("building_type", "Building type", input("building_type"))}
            {field("google_drive_url", "Drive folder link", input("google_drive_url", { type: "url" }))}
          </div>
          <div className="flex items-center gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </Button>
            {err && <span className="text-sm text-red-700">{err}</span>}
          </div>
        </form>
      ) : (
        <dl className="m-0 grid gap-x-8 gap-y-1 text-sm md:grid-cols-2">
          {rows.map(([label, value]) => (
            <div key={label} className="flex justify-between gap-4 border-b border-slate-100 py-1.5">
              <dt className="text-slate-600">{label}</dt>
              <dd className="m-0 text-right text-slate-900">
                {value === null || value === undefined || value === "" ? (
                  <span className="text-orange-800">Not set</span>
                ) : (
                  value
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </Box>
  );
}

// ── Kept from the old overview ──────────────────────────────────────────────

function NotesCard({ projectId, notes }: { projectId: string; notes: NoteSummary[] }) {
  return (
    <Card className="p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Notes</h2>
        <Link href={`/projects/${projectId}/workbench/narratives`} className="text-xs text-blue-600 hover:underline">
          All notes &rarr;
        </Link>
      </div>
      <p className="mt-1 text-xs text-slate-500">Briefs, registers and takeoffs. Each one prints to a BRIO PDF for suppliers.</p>
      {notes.length === 0 ? (
        <p className="mt-3 text-xs text-slate-400">No notes yet.</p>
      ) : (
        <ul className="mt-3 space-y-1">
          {notes.slice(0, 5).map((n) => (
            <li key={n.id}>
              <Link
                href={`/projects/${projectId}/workbench/narratives/${n.id}`}
                className="flex items-baseline justify-between gap-3 px-2 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
              >
                <span className="truncate">{n.title}</span>
                <span className="shrink-0 text-xs text-slate-400">v{n.version_number}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** The per-discipline quantity pages are reachable only from here until the
 *  Takeoff tab is rebuilt (phase 3). */
function QuantityLinks({ projectId }: { projectId: string }) {
  return (
    <Card className="p-4">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Quantity review (drawings)</h2>
      <p className="mt-1 text-xs text-slate-500">
        Open the AI-extracted linear candidates for any discipline. Confirm measurements once; the estimator uses them as authoritative.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {(["architectural", "structural", "mechanical", "electrical", "plumbing", "gc_rollup"] as const).map((d) => (
          <Link
            key={d}
            href={`/projects/${projectId}/quantities/${d}`}
            className="px-3 py-1.5 text-xs uppercase tracking-wide bg-slate-100 text-slate-700 hover:bg-slate-200"
          >
            {d.replace("_", " ")}
          </Link>
        ))}
      </div>
    </Card>
  );
}

function ToggleCard({
  title,
  on,
  label,
  onToggle,
  children,
}: {
  title: string;
  on: boolean;
  label: string;
  onToggle: () => Promise<unknown>;
  children: React.ReactNode;
}) {
  const [err, setErr] = useState<string | null>(null);
  return (
    <Card className="p-4">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{title}</h2>
      <div className="mt-3 flex items-start justify-between gap-4 flex-wrap">
        <div className="text-sm text-slate-600 max-w-xl">{children}</div>
        <button
          type="button"
          onClick={() => onToggle().catch((e) => setErr(errorMessage(e)))}
          className={`px-3 py-1.5 rounded-md text-sm font-semibold border ${
            on ? "bg-emerald-50 border-emerald-300 text-emerald-800" : "bg-slate-100 border-slate-300 text-slate-600"
          }`}
        >
          {label}: {on ? "ON" : "OFF"}
        </button>
      </div>
      {err && <div className="mt-2 text-xs text-red-600">{err}</div>}
    </Card>
  );
}

/** Heuristic: does the project name/type read like a residential dwelling?
 *  Used only to nudge the operator to consider the 2-5-10 flag. */
function looksLikeDwelling(project: ProjectRead): boolean {
  const hay = `${project.name} ${project.type ?? ""}`.toLowerCase();
  const dwelling = /(laneway|dwelling|residence|\bhome\b|\bhouse\b|secondary suite|\bsuite\b|duplex|triplex|adu)/;
  const accessory = /(garage|shop|office|carport|shed|accessory|workshop)/;
  return dwelling.test(hay) && !accessory.test(hay);
}

/** Editable client-facing project summary, shown at the top of the package
 *  Project Overview page. A short narrative describing the project. */
function SummaryCard({
  project,
  onSaved,
}: {
  project: ProjectRead;
  onSaved: (p: ProjectRead) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(project.summary ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const start = () => {
    setDraft(project.summary ?? "");
    setEditing(true);
    setErr(null);
  };

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      const trimmed = draft.trim();
      const updated = await updateProject(project.id, {
        summary: trimmed || null,
      });
      onSaved(updated);
      setEditing(false);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            Project Summary
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Shown at the top of the client package&rsquo;s Project Overview page.
          </p>
        </div>
        {!editing && (
          <button
            type="button"
            onClick={start}
            className="text-sm text-slate-500 hover:text-[var(--color-brio-primary)]"
          >
            {project.summary ? "Edit" : "Add"}
          </button>
        )}
      </div>

      {editing ? (
        <div className="mt-3 space-y-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={4}
            autoFocus
            disabled={saving}
            placeholder="A new detached garage with an office loft at 1234 Example Way…"
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <div className="flex gap-2">
            <Button onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setErr(null);
              }}
              disabled={saving}
            >
              Cancel
            </Button>
          </div>
          {err && <div className="text-xs text-red-600">{err}</div>}
        </div>
      ) : (
        <p className="mt-3 text-sm text-slate-700 whitespace-pre-line">
          {project.summary || (
            <span className="text-slate-400">
              No summary yet — add a short project description for the client
              package.
            </span>
          )}
        </p>
      )}
    </Card>
  );
}

/** Send the client a curated update email (new photos / milestone). */
function NotifyClient({ projectId }: { projectId: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function send(kind: "photos" | "milestone") {
    setBusy(kind);
    setMsg(null);
    try {
      const r = await notifyClient(projectId, kind);
      setMsg(r.recipients > 0 ? `Update sent to the client.` : "No client is set up for this project yet.");
    } catch {
      setMsg("Couldn't send the update.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Notify client</h2>
          <p className="text-xs text-slate-500 mt-0.5">Email the owner a portal update. Share photos first.</p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => send("photos")} disabled={busy !== null}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50">
            {busy === "photos" ? "Sending…" : "📷 New photos"}
          </button>
          <button type="button" onClick={() => send("milestone")} disabled={busy !== null}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50">
            {busy === "milestone" ? "Sending…" : "◈ Milestone update"}
          </button>
        </div>
      </div>
      {msg && <div className="mt-2 text-xs text-emerald-700">{msg}</div>}
    </Card>
  );
}
