"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { errorMessage, workspaceGet } from "@/lib/workspaceApi";

/** Settings (round 2 board R2Settings): one place for team & access, company,
 *  AI, how we work, writing rules, review requests and email. Sections that
 *  already have a page link to it. */

type Overview = {
  people: { email: string; name: string | null; role: string; active: boolean; projects: number }[] | null;
  roles: { what: string; superadmin: string; manager: string; worker: string; owner: string }[];
  company: { name: string; editable: boolean };
  email: { sender: string | null; inbound_domain: string; project_addresses: { project: string; address: string }[] };
};

const SECTIONS = [
  { key: "team", label: "Team & access", sub: "people, roles, which projects" },
  { key: "company", label: "Company", sub: "name, documents, payment terms" },
  { key: "ai", label: "AI", sub: "budget, approval limits, Mac worker", href: "/ai-setup" },
  { key: "work", label: "How we work", sub: "practices, suppliers, forming defaults", href: "/ai-setup" },
  { key: "rules", label: "Writing rules", sub: "today's Skills · owner only", href: "/skills" },
  { key: "reviews", label: "Review requests", sub: "today's Reviews", href: "/reviews" },
  { key: "email", label: "Email", sub: "project addresses, sender" },
] as const;

const ROLE_LABEL: Record<string, string> = { superadmin: "Admin (all access)", manager: "Manager", worker: "Crew", owner: "Client" };

export default function SettingsPage() {
  const [o, setO] = useState<Overview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [on, setOn] = useState<string>("team");
  useEffect(() => { workspaceGet<Overview>("/settings/overview").then(setO, (e) => setErr(errorMessage(e))); }, []);

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-2xl font-semibold">Settings</h1>
      {err && <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{err}</div>}
      <div className="flex flex-wrap items-start gap-6">
        <nav aria-label="Settings sections" className="flex flex-[0_1_220px] flex-col gap-0.5">
          {SECTIONS.map((s) => {
            const body = (<><span className="text-sm">{s.label}</span><span className="text-xs text-slate-500">{s.sub}</span></>);
            const cls = `flex flex-col rounded px-3 py-2 text-left ${on === s.key ? "border border-slate-300 bg-white font-semibold" : "hover:bg-slate-100"}`;
            return "href" in s
              ? <Link key={s.key} href={s.href} className={cls}>{body}</Link>
              : <button key={s.key} type="button" className={cls} aria-current={on === s.key ? "page" : undefined} onClick={() => setOn(s.key)}>{body}</button>;
          })}
        </nav>

        <div className="flex min-w-0 flex-[999_1_560px] flex-col gap-4">
          {!o && !err && <p className="text-sm text-slate-500">Loading…</p>}
          {o && on === "team" && (
            <>
              {o.people && (
                <section className="overflow-x-auto rounded border border-slate-200 bg-white">
                  <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3">
                    <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-600">People · {o.people.length}</h2>
                    <Link href="/admin/users" className="rounded bg-[var(--color-brio-primary)] px-3 py-1 text-xs font-medium text-white">Invite or edit</Link>
                  </div>
                  <table className="mt-2 w-full min-w-[560px] text-sm">
                    <thead className="text-xs uppercase tracking-wide text-slate-600"><tr>
                      <th className="px-4 py-2 text-left">Person</th><th className="px-4 py-2 text-left">Role</th><th className="px-4 py-2 text-left">Projects</th><th className="px-4 py-2 text-left">Status</th>
                    </tr></thead>
                    <tbody>
                      {o.people.map((p) => (
                        <tr key={p.email} className="border-t border-slate-100">
                          <td className="px-4 py-2">{p.name && <b>{p.name} </b>}<span className="text-slate-600">{p.email}</span></td>
                          <td className="px-4 py-2">{ROLE_LABEL[p.role] ?? p.role}</td>
                          <td className="px-4 py-2">{p.role === "superadmin" ? "All" : p.projects}</td>
                          <td className="px-4 py-2">{p.active ? "Active" : <span className="text-slate-500">Switched off</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              )}
              <section className="overflow-x-auto rounded border border-slate-200 bg-white px-4 py-3">
                <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-600">What each role can do</h2>
                <table className="mt-2 w-full min-w-[620px] text-sm">
                  <thead className="text-xs uppercase tracking-wide text-slate-600"><tr>
                    <th className="py-2 text-left" />{["superadmin", "manager", "worker", "owner"].map((r) => <th key={r} className="py-2 text-left">{ROLE_LABEL[r]}</th>)}
                  </tr></thead>
                  <tbody>
                    {o.roles.map((r) => (
                      <tr key={r.what} className="border-t border-slate-100">
                        <td className="py-2 pr-3">{r.what}</td><td className="py-2 pr-3">{r.superadmin}</td><td className="py-2 pr-3">{r.manager}</td>
                        <td className="py-2 pr-3">{r.worker}</td><td className="py-2">{r.owner}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-2 text-xs text-slate-500">Read-only. It describes what the app already enforces.</p>
              </section>
            </>
          )}
          {o && on === "company" && (
            <section className="space-y-2 rounded border border-slate-200 bg-white px-4 py-3 text-sm">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-600">Company</h2>
              <p><b>{o.company.name}</b></p>
              <p className="text-slate-600">Phone, GST number and logo are set in the document templates (estimates, invoices, contracts). Changing them here isn&apos;t built yet; ask for a change and it&apos;s made in the templates.</p>
              <p className="text-slate-600">Payment terms templates are on each project&apos;s Money › Contract page (Payment terms).</p>
            </section>
          )}
          {o && on === "email" && (
            <section className="space-y-2 rounded border border-slate-200 bg-white px-4 py-3 text-sm">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-600">Email</h2>
              <p>Sent from: <b>{o.email.sender ?? "not set"}</b></p>
              <p className="text-slate-600">Each project has its own address. BCC it on mail you send, or forward mail to it, and the mail is filed in that project&apos;s Conversation.</p>
              {o.email.project_addresses.length === 0 ? <p className="text-slate-500">No project addresses made yet. One is made the first time a project&apos;s Conversation is opened.</p> : (
                <ul className="divide-y divide-slate-100">
                  {o.email.project_addresses.map((a) => (
                    <li key={a.address} className="flex flex-wrap gap-2 py-1.5"><span className="min-w-40">{a.project}</span><code className="break-all text-xs">{a.address}</code></li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
