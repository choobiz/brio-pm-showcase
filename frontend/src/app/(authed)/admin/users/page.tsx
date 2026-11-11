"use client";

import { useEffect, useState } from "react";

import { Card } from "@/components/ui/card";
import {
  ROLES,
  ROLE_LABEL,
  type AppUser,
  listUsers,
  createUser,
  updateUser,
  deactivateUser,
  listProjectsLite,
} from "@/lib/users";

export default function UsersAdmin() {
  const [users, setUsers] = useState<AppUser[] | null>(null);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [denied, setDenied] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Invite form
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("worker");
  const [assign, setAssign] = useState<string[]>([]);
  // Editing one person's projects after the invite (Amir, 2026-10-15). Before
  // this, a new project could only be given to the team by editing the database.
  const [editing, setEditing] = useState<{ id: string; projects: string[] } | null>(null);

  const load = () =>
    listUsers()
      .then(setUsers)
      .catch((e: Error & { status?: number }) => {
        if (e.status === 403) setDenied(true);
        else setErr(String(e));
      });

  useEffect(() => {
    load();
    listProjectsLite().then(setProjects).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (denied)
    return (
      <div className="max-w-lg rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        You don&apos;t have access to user management. This section is for superadmins.
      </div>
    );

  async function onInvite() {
    setErr(null);
    if (!email.trim()) return;
    setBusy(true);
    try {
      await createUser({ email: email.trim(), name: name.trim() || undefined, role, project_ids: assign });
      setEmail(""); setName(""); setRole("worker"); setAssign([]);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Invite failed");
    } finally {
      setBusy(false);
    }
  }

  async function patch(u: AppUser, body: Parameters<typeof updateUser>[1]) {
    setErr(null);
    try {
      await updateUser(u.id, body);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Update failed");
    }
  }

  const projName = (id: string) => projects.find((p) => p.id === id)?.name ?? id.slice(0, 8);

  async function saveProjects() {
    if (!editing) return;
    const u = users?.find((x) => x.id === editing.id);
    if (u) await patch(u, { project_ids: editing.projects });
    setEditing(null);
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>
          Users &amp; Roles
        </h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Invite people, set their role, and assign them to projects. Only active users can sign in.
        </p>
      </div>

      {err && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>
      )}

      {/* Invite */}
      <Card className="p-4 space-y-3">
        <div className="text-sm font-semibold uppercase tracking-wide text-slate-500">Invite a user</div>
        <div className="grid gap-3 md:grid-cols-4">
          <input
            type="email" placeholder="email@example.com" value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1.5 text-sm md:col-span-2"
          />
          <input
            type="text" placeholder="Name (optional)" value={name}
            onChange={(e) => setName(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1.5 text-sm"
          />
          <select
            value={role} onChange={(e) => setRole(e.target.value)}
            className="border border-slate-300 rounded px-2 py-1.5 text-sm"
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>{ROLE_LABEL[r]}</option>
            ))}
          </select>
        </div>
        {role !== "superadmin" && projects.length > 0 && (
          <div>
            <div className="text-xs text-slate-500 mb-1">
              Assign to projects {role === "owner" ? "(the client's own project)" : "(scopes their access)"}:
            </div>
            <div className="flex flex-wrap gap-1.5">
              {projects.map((p) => {
                const on = assign.includes(p.id);
                return (
                  <button
                    key={p.id} type="button"
                    onClick={() => setAssign((a) => (on ? a.filter((x) => x !== p.id) : [...a, p.id]))}
                    className={`rounded-full border px-2.5 py-1 text-xs ${
                      on ? "border-blue-400 bg-blue-50 text-blue-800" : "border-slate-200 text-slate-500"
                    }`}
                  >
                    {p.name}
                  </button>
                );
              })}
            </div>
          </div>
        )}
        <button
          type="button" onClick={onInvite} disabled={busy || !email.trim()}
          className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? "Inviting…" : "Send invite"}
        </button>
        <p className="text-xs text-slate-400">
          They&apos;ll sign in with a magic link sent to this email. Superadmins have full global access
          (no project assignment needed).
        </p>
      </Card>

      {/* Users table */}
      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-2">User</th>
              <th className="px-4 py-2">Role</th>
              <th className="px-4 py-2">Projects</th>
              <th className="px-4 py-2">Status</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {users?.map((u) => (
              <tr key={u.id} className="border-t border-slate-100">
                <td className="px-4 py-2">
                  <div className="font-medium text-slate-800">{u.name || u.email}</div>
                  {u.name && <div className="text-xs text-slate-400">{u.email}</div>}
                </td>
                <td className="px-4 py-2">
                  <select
                    value={u.role} onChange={(e) => patch(u, { role: e.target.value })}
                    className="border border-slate-300 rounded px-1.5 py-1 text-xs"
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>{ROLE_LABEL[r]}</option>
                    ))}
                  </select>
                </td>
                <td className="px-4 py-2 text-xs text-slate-600">
                  {u.role === "superadmin" ? (
                    "All (global)"
                  ) : editing?.id === u.id ? (
                    <div className="space-y-2">
                      <div className="flex max-h-48 flex-wrap gap-1.5 overflow-y-auto">
                        {projects.map((p) => {
                          const on = editing.projects.includes(p.id);
                          return (
                            <button
                              key={p.id} type="button"
                              onClick={() =>
                                setEditing({
                                  id: u.id,
                                  projects: on
                                    ? editing.projects.filter((x) => x !== p.id)
                                    : [...editing.projects, p.id],
                                })
                              }
                              className={`rounded-full border px-2.5 py-1 text-xs ${
                                on ? "border-blue-400 bg-blue-50 text-blue-800" : "border-slate-200 text-slate-500"
                              }`}
                            >
                              {p.name}
                            </button>
                          );
                        })}
                      </div>
                      <div className="flex gap-3">
                        <button type="button" onClick={saveProjects}
                          className="rounded bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700">
                          Save projects
                        </button>
                        <button type="button" onClick={() => setEditing(null)}
                          className="text-xs text-slate-500 hover:underline">
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span>{u.project_ids.length ? u.project_ids.map(projName).join(", ") : "None"}</span>
                      <button type="button" onClick={() => setEditing({ id: u.id, projects: u.project_ids })}
                        className="text-xs text-blue-600 hover:underline">
                        Edit projects
                      </button>
                    </div>
                  )}
                </td>
                <td className="px-4 py-2">
                  {u.active ? (
                    <span className="text-emerald-600 text-xs font-medium">Active</span>
                  ) : (
                    <span className="text-slate-400 text-xs">Inactive</span>
                  )}
                </td>
                <td className="px-4 py-2 text-right">
                  {u.active ? (
                    <button
                      type="button" onClick={() => deactivateUser(u.id).then(load).catch((e) => setErr(String(e)))}
                      className="text-xs text-red-600 hover:underline"
                    >
                      Deactivate
                    </button>
                  ) : (
                    <button
                      type="button" onClick={() => patch(u, { active: true })}
                      className="text-xs text-blue-600 hover:underline"
                    >
                      Reactivate
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {users && users.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400">No users yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
