"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { getUnreadCount } from "@/lib/messages";
import { getProject, type ProjectRead } from "@/lib/projects";

/**
 * The project's nine sections, as tabs across the top (wireframe round 1,
 * 2026-10-10, approved 2026-10-12). Replaces the 19-item ProjectSidebar.
 *
 * Each tab still opens the EXISTING page for now. A tab that gathers several
 * old pages (Site, Money, Client) shows them as a second row until that tab is
 * rebuilt as one page; nothing is unreachable in the meantime. Pages with no
 * tab in the design yet (Notes, Presentation, Project index) sit under More.
 *
 * `match` lists extra path prefixes that belong to a tab without being in its
 * second row, e.g. the per-discipline quantity pages belong to Takeoff.
 */

type Page = { path: string; label: string; also?: string[] };
type Tab = { label: string; pages: Page[]; match?: string[] };

const TABS: Tab[] = [
  { label: "Overview", pages: [{ path: "/overview", label: "Overview" }] },
  {
    label: "Documents",
    pages: [
      { path: "/documents", label: "Documents" },
      { path: "/prepare", label: "Prepare (orders, takeoffs)" },
      { path: "/submittals", label: "Submittals" },
    ],
  },
  { label: "Scope", pages: [{ path: "/scope", label: "Scope" }] },
  {
    label: "Takeoff",
    pages: [
      { path: "/takeoff", label: "Takeoff" },
      { path: "/quantities/architectural", label: "Measure on a PDF" },
    ],
    match: ["/quantities"],
  },
  {
    label: "Estimate",
    pages: [{ path: "/estimate", label: "Estimate" }],
    // Compare and the line editor still live under the old path.
    match: ["/workbench/estimates"],
  },
  // Round 2 (approved 2026-10-29): Milestones moved in as "Bookings & letters" —
  // who to book, by when, and the documents each inspection needs.
  {
    label: "Schedule",
    pages: [
      { path: "/workbench/schedule", label: "Plan" },
      { path: "/schedule/record", label: "Record what happened" },
      { path: "/milestones", label: "Bookings & letters" },
    ],
  },
  {
    label: "Site",
    pages: [{ path: "/site", label: "Site" }],
    // A single report's page (linked from Site, carries the PDF).
    match: ["/daily-reports"],
  },
  {
    label: "Money",
    pages: [
      { path: "/money", label: "Money" },
      // Round 2 (Amir 2026-10-23): two pages, one sub-tab; switched on the page.
      { path: "/workbench/cashflow", label: "Cash flow & SOV", also: ["/workbench/sov"] },
      { path: "/workbench/contract", label: "Contract" },
    ],
    match: ["/invoices", "/change-orders", "/workbench/sov"],
  },
  {
    label: "Client",
    pages: [
      // Round 2 (2026-11-02): Messages + Conversations are one Conversation.
      { path: "/conversation", label: "Conversation" },
      { path: "/selections", label: "Selections & approvals" },
      // Round 2 (2026-11-02): Presentation moved out of More, as Proposal.
      { path: "/workbench/presentation", label: "Proposal" },
    ],
    match: ["/messages", "/conversations"],
  },
];

const MORE: Page[] = [
  { path: "/workbench/narratives", label: "Notes" },
  { path: "/index", label: "Project index" },
];

function under(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(href + "/");
}

export function ProjectTabs({ id }: { id: string }) {
  const pathname = usePathname();
  const root = `/projects/${id}`;
  const [proj, setProj] = useState<ProjectRead | null>(null);
  const [unread, setUnread] = useState(0);
  const moreRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    let live = true;
    getProject(id).then((p) => live && setProj(p)).catch(() => {});
    return () => {
      live = false;
    };
  }, [id]);

  useEffect(() => {
    let live = true;
    const load = () =>
      getUnreadCount(id)
        .then((r) => live && setUnread(r.count))
        .catch(() => {});
    load();
    // Opening Messages marks them read server-side, so refresh on navigation
    // and when returning to the browser tab.
    window.addEventListener("focus", load);
    return () => {
      live = false;
      window.removeEventListener("focus", load);
    };
  }, [id, pathname]);

  // Close the More menu after choosing from it.
  useEffect(() => {
    moreRef.current?.removeAttribute("open");
  }, [pathname]);

  const active = TABS.find((t) =>
    [...t.pages.map((p) => p.path), ...(t.match ?? [])].some((p) => under(pathname, root + p)),
  );
  const inMore = !active && MORE.some((p) => under(pathname, root + p.path));

  const tabClass = (on: boolean) =>
    `inline-flex min-h-11 items-center gap-1.5 whitespace-nowrap border-b-2 px-3.5 text-sm ${
      on
        ? "border-slate-900 font-semibold text-slate-900"
        : "border-transparent text-slate-600 hover:text-slate-900"
    }`;

  return (
    <header className="mb-5 space-y-2.5">
      <div className="text-[13px]">
        <Link href="/projects" className="text-blue-700 hover:text-blue-900">
          Projects
        </Link>
        {proj && <span className="text-slate-500"> / {proj.name}</span>}
      </div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="m-0 text-2xl font-bold text-slate-900">{proj?.name ?? " "}</h1>
        {proj?.address && <span className="text-sm text-slate-500">{proj.address}</span>}
      </div>

      <nav aria-label="Project" className="flex flex-wrap items-end gap-0.5 border-b border-slate-300">
        {TABS.map((t) => {
          const on = t === active;
          const badge = t.label === "Client" && unread > 0 ? unread : 0;
          return (
            <Link
              key={t.label}
              href={root + t.pages[0].path}
              aria-current={on ? "page" : undefined}
              className={tabClass(on)}
            >
              {t.label}
              {badge > 0 && (
                <span
                  className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-red-600 px-1 text-[11px] font-semibold text-white"
                  title={`${badge} unread client message${badge > 1 ? "s" : ""}`}
                >
                  {badge}
                </span>
              )}
            </Link>
          );
        })}
        <details ref={moreRef} className="relative">
          <summary className={`${tabClass(inMore)} cursor-pointer list-none`}>More ▾</summary>
          <div className="absolute right-0 z-20 mt-1 min-w-48 rounded-md border border-slate-300 bg-white py-1 shadow-md">
            {MORE.map((p) => (
              <Link
                key={p.path}
                href={root + p.path}
                className={`block px-3 py-2 text-sm hover:bg-slate-100 ${
                  under(pathname, root + p.path) ? "font-semibold text-slate-900" : "text-slate-700"
                }`}
              >
                {p.label}
              </Link>
            ))}
          </div>
        </details>
      </nav>

      {active && active.pages.length > 1 && (
        <nav aria-label={`${active.label} pages`} className="flex flex-wrap gap-1.5 pt-1">
          {active.pages.map((p) => {
            const on = [p.path, ...(p.also ?? [])].some((x) => under(pathname, root + x));
            return (
              <Link
                key={p.path}
                href={root + p.path}
                aria-current={on ? "page" : undefined}
                className={`rounded-md border px-3 py-1.5 text-[13px] ${
                  on
                    ? "border-slate-900 bg-slate-900 text-white"
                    : "border-slate-300 bg-white text-slate-700 hover:border-slate-500"
                }`}
              >
                {p.label}
              </Link>
            );
          })}
        </nav>
      )}
    </header>
  );
}
