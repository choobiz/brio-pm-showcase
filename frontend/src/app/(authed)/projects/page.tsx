"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  PROJECT_STATUSES,
  PROJECT_TYPES,
  PROJECT_TYPE_LABELS,
  STATUS_LABELS,
  archiveProject,
  createProject,
  listProjects,
  unarchiveProject,
  updateProject,
  type ProjectRead,
  type ProjectStatus,
  type ProjectType,
} from "@/lib/projects";

// Phase 7 polish — every status except `archived` is shown by default so
// the user lands on actionable projects and has to opt in to archive view.
const DEFAULT_STATUS_FILTER: Set<ProjectStatus> = new Set([
  "lead",
  "preconstruction",
  "in_progress",
  "closeout",
]);

type SortKey = "updated" | "name" | "status";

const STATUS_COLOR: Record<string, string> = {
  lead: "bg-slate-100 text-slate-700",
  preconstruction: "bg-blue-100 text-blue-800",
  in_progress: "bg-emerald-100 text-emerald-800",
  closeout: "bg-amber-100 text-amber-800",
  archived: "bg-slate-200 text-slate-500",
};

export default function ProjectsPage() {
  const [projects, setProjects] = useState<ProjectRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [statusFilter, setStatusFilter] = useState<Set<ProjectStatus>>(
    new Set(DEFAULT_STATUS_FILTER),
  );
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("updated");
  const [busyId, setBusyId] = useState<string | null>(null);
  // "No type" is a filter of its own because 17 projects lacked one on
  // 2026-10-12 and Home sends you here to fix them.
  const [untypedOnly, setUntypedOnly] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);

  // Type, status and year built are edited in the row (wireframe, Projects
  // board). The row updates from the server's answer, never optimistically,
  // so a refused value (e.g. a year outside 1850-2100) cannot look saved.
  const saveField = useCallback(
    async (p: ProjectRead, patch: Parameters<typeof updateProject>[1]) => {
      setSavingId(p.id);
      try {
        const next = await updateProject(p.id, patch);
        setProjects((all) => all.map((x) => (x.id === p.id ? next : x)));
        setError(null);
      } catch (e) {
        setError(`Could not save ${p.name}: ${String(e).replace(/^Error: /, "")}`);
      } finally {
        setSavingId(null);
      }
    },
    [],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setProjects(await listProjects());
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // Archive is reversible: it retains the prior status server-side so Unarchive
  // restores it exactly. Archiving drops the project out of the default view
  // (which hides `archived`).
  const runArchive = useCallback(
    async (p: ProjectRead, archive: boolean, confirmMsg?: string) => {
      if (confirmMsg && !window.confirm(confirmMsg)) return;
      setBusyId(p.id);
      try {
        await (archive ? archiveProject(p.id) : unarchiveProject(p.id));
        await refresh();
      } catch (e) {
        setError(String(e));
      } finally {
        setBusyId(null);
      }
    },
    [refresh],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = projects.filter((p) => {
      if (!statusFilter.has(p.status as ProjectStatus)) return false;
      if (untypedOnly && p.type) return false;
      if (q) {
        const hay = [p.name, p.address ?? "", p.type ?? "", p.slug]
          .join(" ")
          .toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    out.sort((a, b) => {
      if (sortKey === "name") return a.name.localeCompare(b.name);
      if (sortKey === "status")
        return (a.status ?? "").localeCompare(b.status ?? "");
      return (b.updated_at ?? b.created_at ?? "").localeCompare(
        a.updated_at ?? a.created_at ?? "",
      );
    });
    return out;
  }, [projects, statusFilter, query, sortKey, untypedOnly]);

  const untypedCount = projects.filter((p) => !p.type && p.status !== "archived").length;

  function toggleStatus(s: ProjectStatus) {
    const next = new Set(statusFilter);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    setStatusFilter(next);
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1
          className="text-2xl font-semibold"
          style={{ color: "var(--color-brio-accent)" }}
        >
          Projects
          <span className="ml-2 text-sm font-normal text-slate-500">
            {visible.length} of {projects.length}
          </span>
        </h1>
        <Button onClick={() => setShowCreate(true)}>New Project</Button>
      </div>

      {/* Filter / search / sort bar */}
      <div className="flex flex-wrap gap-3 items-center text-xs">
        <div className="flex gap-1 flex-wrap">
          {PROJECT_STATUSES.map((s) => {
            const on = statusFilter.has(s);
            return (
              <button
                key={s}
                type="button"
                onClick={() => toggleStatus(s)}
                className={`px-2 py-0.5 rounded text-[10px] uppercase tracking-wide font-semibold transition ${
                  on
                    ? STATUS_COLOR[s] ?? "bg-slate-100 text-slate-700"
                    : "bg-white text-slate-400 border border-slate-200 hover:border-slate-300"
                }`}
              >
                {s.replace("_", " ")}
              </button>
            );
          })}
          {untypedCount > 0 && (
            <button
              type="button"
              onClick={() => setUntypedOnly((v) => !v)}
              aria-pressed={untypedOnly}
              className={`ml-1 px-2 py-0.5 rounded text-[10px] uppercase tracking-wide font-semibold border ${
                untypedOnly
                  ? "bg-orange-700 border-orange-700 text-white"
                  : "bg-white border-orange-700 text-orange-800"
              }`}
            >
              No type · {untypedCount}
            </button>
          )}
        </div>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search name / address / type…"
          className="border border-slate-300 rounded px-2 py-1 text-xs min-w-0 flex-1 min-w-[180px] max-w-md"
        />
        <label className="flex items-center gap-1 text-slate-500">
          Sort:
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="border border-slate-300 rounded px-2 py-1 text-xs"
          >
            <option value="updated">Most recently updated</option>
            <option value="name">Name (A→Z)</option>
            <option value="status">Status</option>
          </select>
        </label>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <Card className="p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 text-left text-slate-500">
            <tr>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Address</th>
              <th className="px-4 py-3">Type</th>
              <th className="px-4 py-3">Year built</th>
              <th className="px-4 py-3 text-right">SF</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center text-slate-400">
                  Loading…
                </td>
              </tr>
            ) : visible.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center text-slate-400">
                  {projects.length === 0
                    ? "No projects yet."
                    : "No projects match the current filters."}
                </td>
              </tr>
            ) : (
              visible.map((p) => (
                <tr
                  key={p.id}
                  className="border-b border-slate-100 hover:bg-slate-50"
                >
                  <td className="px-4 py-3">
                    <Link
                      href={`/projects/${p.id}/overview`}
                      className="font-medium text-[var(--color-brio-primary)] hover:underline"
                    >
                      {p.name}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{p.address ?? "—"}</td>
                  <td className="px-4 py-2">
                    <select
                      aria-label={`Type of ${p.name}`}
                      value={p.type ?? ""}
                      disabled={savingId === p.id}
                      onChange={(e) => e.target.value && saveField(p, { type: e.target.value })}
                      className={`max-w-[220px] rounded border px-2 py-1 text-xs ${
                        p.type ? "border-slate-300 text-slate-700" : "border-orange-700 text-orange-800"
                      }`}
                    >
                      {!p.type && <option value="">Set type…</option>}
                      {PROJECT_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {PROJECT_TYPE_LABELS[t]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-4 py-2">
                    <YearInput
                      project={p}
                      disabled={savingId === p.id}
                      onSave={(y) => saveField(p, { building_age: y })}
                    />
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600">
                    {p.sqft ? p.sqft.toLocaleString() : "—"}
                  </td>
                  <td className="px-4 py-2">
                    {p.status === "archived" ? (
                      <span className="px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-slate-200 text-slate-500">
                        archived
                      </span>
                    ) : (
                      <select
                        aria-label={`Status of ${p.name}`}
                        value={p.status}
                        disabled={savingId === p.id}
                        onChange={(e) => saveField(p, { status: e.target.value as ProjectStatus })}
                        className={`rounded px-2 py-1 text-xs font-semibold ${
                          STATUS_COLOR[p.status] ?? "bg-slate-100 text-slate-700"
                        }`}
                      >
                        {PROJECT_STATUSES.filter((st) => st !== "archived").map((st) => (
                          <option key={st} value={st}>
                            {STATUS_LABELS[st]}
                          </option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {p.status === "archived" ? (
                      <button
                        type="button"
                        disabled={busyId === p.id}
                        onClick={() => runArchive(p, false)}
                        title={
                          p.pre_archive_status
                            ? `Restore to "${p.pre_archive_status.replace("_", " ")}"`
                            : "Restore to lead"
                        }
                        className="text-xs text-slate-500 hover:text-slate-800 disabled:opacity-50"
                      >
                        {busyId === p.id ? "…" : "Unarchive"}
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={busyId === p.id}
                        onClick={() =>
                          runArchive(
                            p,
                            true,
                            `Archive "${p.name}"? It will be hidden from the default view — toggle the "archived" filter to see it again.`,
                          )
                        }
                        className="text-xs text-slate-400 hover:text-red-600 disabled:opacity-50"
                      >
                        {busyId === p.id ? "…" : "Archive"}
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>

      {showCreate && (
        <CreateProjectModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            refresh();
          }}
        />
      )}
    </div>
  );
}

/** Year built, saved on blur or Enter. Blank leaves it unset. */
function YearInput({
  project,
  disabled,
  onSave,
}: {
  project: ProjectRead;
  disabled: boolean;
  onSave: (year: number) => void;
}) {
  const [value, setValue] = useState(project.building_age?.toString() ?? "");
  useEffect(() => setValue(project.building_age?.toString() ?? ""), [project.building_age]);
  const commit = () => {
    const y = Number(value);
    if (!value.trim() || y === project.building_age) return;
    onSave(y);
  };
  return (
    <input
      aria-label={`Year built of ${project.name}`}
      inputMode="numeric"
      placeholder="Add"
      value={value}
      disabled={disabled}
      onChange={(e) => setValue(e.target.value.replace(/[^0-9]/g, "").slice(0, 4))}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      className="w-16 rounded border border-slate-300 px-2 py-1 text-xs tabular-nums placeholder:text-slate-400"
    />
  );
}

function CreateProjectModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  // Project type is now a fixed set, not free text. It selects the cost-division
  // set an estimate is organised into (residential -> BRIO's 18 numbered
  // divisions, commercial -> CSI MasterFormat), so a typo or a prose description
  // would send the estimate to the generic fallback list. The old free-text field
  // is why one project's type read "Commercial Renovation of an office site".
  const [type, setType] = useState<ProjectType | "">("");
  const [buildingAge, setBuildingAge] = useState("");
  const [statusValue, setStatusValue] = useState<ProjectStatus>("lead");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (!name.trim()) {
      setErr("Project name is required.");
      return;
    }
    if (!type) {
      setErr("Project type is required — it decides which cost divisions estimates use.");
      return;
    }
    const year = Number(buildingAge);
    if (!buildingAge.trim() || !Number.isInteger(year) || year < 1850 || year > 2100) {
      setErr("Year built is required, as a 4-digit year between 1850 and 2100.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await createProject({
        name: name.trim(),
        address: address.trim() || undefined,
        type,
        building_age: year,
        status: statusValue,
      });
      onCreated();
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={busy ? undefined : onClose}
    >
      <Card
        className="w-full max-w-md space-y-3 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2
          className="text-lg font-semibold"
          style={{ color: "var(--color-brio-accent)" }}
        >
          New Project
        </h2>
        <label className="block text-sm text-slate-700">
          Name
          <Input value={name} disabled={busy} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="block text-sm text-slate-700">
          Site address
          <Input
            value={address}
            disabled={busy}
            onChange={(e) => setAddress(e.target.value)}
          />
        </label>
        <label className="block text-sm text-slate-700">
          Project type
          <select
            value={type}
            disabled={busy}
            onChange={(e) => setType(e.target.value as ProjectType)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">Select a type…</option>
            {PROJECT_TYPES.map((t) => (
              <option key={t} value={t}>
                {PROJECT_TYPE_LABELS[t]}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-xs text-slate-500">
            Decides the cost divisions estimates use: residential uses BRIO&rsquo;s 18
            numbered divisions, commercial uses CSI MasterFormat.
          </span>
        </label>
        <label className="block text-sm text-slate-700">
          Year built
          <Input
            value={buildingAge}
            disabled={busy}
            inputMode="numeric"
            placeholder="e.g. 1978"
            onChange={(e) => setBuildingAge(e.target.value)}
          />
          <span className="mt-1 block text-xs text-slate-500">
            Drives hazmat exposure — pre-1990 stock needs testing allowed for.
          </span>
        </label>
        <label className="block text-sm text-slate-700">
          Status
          <select
            value={statusValue}
            disabled={busy}
            onChange={(e) => setStatusValue(e.target.value as ProjectStatus)}
            className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            {PROJECT_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        {err && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {err}
          </div>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="secondary" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? "Creating…" : "Create Project"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
