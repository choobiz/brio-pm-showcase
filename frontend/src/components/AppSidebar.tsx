"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ASK_EVENT } from "./AskPanel";
import { NavIcon } from "./NavIcon";

/**
 * App-level navigation.
 *
 * Collapsed to an icon rail BY DEFAULT, and it is the app sidebar rather than
 * the project one that gives way: once you are inside a project, this list is
 * nine destinations you already know, while the project nav is the one you are
 * actually reading. Two expanded sidebars were taking 464px before the page
 * began — enough that a document list had no room to show a filename.
 *
 * The choice is remembered, so anyone who prefers labels sets it once.
 */
const STORAGE_KEY = "brio.appnav.collapsed";
/** Phone width: the saved desktop preference does not apply. */
const narrow = () => typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches;

type Child = { href: string; label: string };
/** A group (Libraries, Settings) opens its first page and lists the rest
 *  under it while you're in one of them. */
type Item = { href: string; label: string; icon: string; children?: Child[] };

const under = (pathname: string, href: string) => pathname === href || pathname.startsWith(href + "/");

export function AppSidebar({
  items,
  footer,
}: {
  items: Item[];
  footer: React.ReactNode;
}) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(true);
  const [ready, setReady] = useState(false);
  const navRef = useRef<HTMLElement>(null);
  // Close an open group menu once a page in it is chosen.
  useEffect(() => {
    navRef.current?.querySelectorAll("details[open]").forEach((d) => d.removeAttribute("open"));
  }, [pathname]);

  useEffect(() => {
    // On a phone the menu always starts collapsed: an open desktop preference
    // took half a 400 px screen (rollout test 2026-10-14).
    if (narrow()) {
      setReady(true);
      return;
    }
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved !== null) setCollapsed(saved === "1");
    } catch {
      /* private mode — the default stands */
    }
    setReady(true);
  }, []);

  const toggle = () => {
    setCollapsed((c) => {
      const next = !c;
      if (!narrow()) {
        try {
          window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
        } catch {
          /* ignore */
        }
      }
      return next;
    });
  };

  return (
    <aside
      className={`flex shrink-0 flex-col border-r border-slate-200 bg-white transition-[width] duration-150 ${
        collapsed ? "w-14 px-2 py-3" : "w-56 p-4"
      } ${ready ? "" : "invisible"}`}
    >
      <div className={`mb-5 flex items-center ${collapsed ? "justify-center" : "gap-2"}`}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/brio-logo.png" alt="BRIO" className="h-7 w-7 shrink-0" />
        {!collapsed && (
          <span className="font-semibold" style={{ color: "var(--color-brio-accent)" }}>
            Keystone
          </span>
        )}
      </div>

      {/* Ask (phase 9): opens the panel mounted in the layout; also ⌘K. */}
      <button
        type="button"
        onClick={() => window.dispatchEvent(new Event(ASK_EVENT))}
        title="Ask (⌘K)"
        aria-label="Ask"
        className={`mb-3 flex w-full items-center rounded-md border border-slate-300 text-sm text-slate-800 hover:bg-slate-100 ${
          collapsed ? "justify-center py-2" : "gap-2.5 px-3 py-2"
        }`}
      >
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" className="h-[18px] w-[18px] shrink-0" aria-hidden>
          <path d="M4 4h12v9H8l-4 3V4z" strokeLinejoin="round" />
          <path d="M8.2 7.4a1.9 1.9 0 1 1 2.6 1.8c-.5.2-.8.6-.8 1.1M10 11.6v.1" strokeLinecap="round" />
        </svg>
        {!collapsed && (
          <>
            <span>Ask</span>
            <span className="ml-auto text-xs text-slate-400">⌘K</span>
          </>
        )}
      </button>

      <nav ref={navRef} className="space-y-0.5 text-sm">
        {items.map((item) => {
          const active = item.children
            ? item.children.some((c) => under(pathname, c.href))
            : under(pathname, item.href);
          if (item.children && collapsed) {
            // Icon rail: the group opens a small menu of its pages.
            return (
              <details key={item.label} className="relative">
                <summary
                  title={item.label}
                  aria-label={item.label}
                  className={`flex cursor-pointer list-none items-center justify-center rounded-md px-0 py-2 ${
                    active ? "bg-blue-50 text-blue-800" : "text-slate-700 hover:bg-slate-100"
                  }`}
                >
                  <NavIcon name={item.icon} className="h-[18px] w-[18px] shrink-0" />
                </summary>
                <div className="absolute left-12 top-0 z-30 min-w-48 rounded-md border border-slate-300 bg-white py-1 shadow-md">
                  <div className="px-3 py-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{item.label}</div>
                  {item.children.map((c) => (
                    <Link key={c.href} href={c.href}
                      className={`block px-3 py-2 text-sm hover:bg-slate-100 ${under(pathname, c.href) ? "font-semibold text-blue-800" : "text-slate-700"}`}>
                      {c.label}
                    </Link>
                  ))}
                </div>
              </details>
            );
          }
          return (
            <div key={item.label}>
            <Link
              key={item.href}
              href={item.href}
              title={item.label}
              aria-label={item.label}
              className={`flex items-center rounded-md ${
                collapsed ? "justify-center px-0 py-2" : "gap-2.5 px-3 py-2"
              } ${active ? "bg-blue-50 text-blue-800" : "text-slate-700 hover:bg-slate-100"}`}
            >
              <NavIcon name={item.icon} className="h-[18px] w-[18px] shrink-0" />
              {!collapsed && <span className="truncate">{item.label}</span>}
            </Link>
            {item.children && active && !collapsed && (
              <div className="mt-0.5 space-y-0.5">
                {item.children.map((c) => (
                  <Link
                    key={c.href}
                    href={c.href}
                    aria-current={under(pathname, c.href) ? "page" : undefined}
                    className={`block rounded-md py-1.5 pl-10 pr-3 text-[13px] ${
                      under(pathname, c.href) ? "font-semibold text-blue-800" : "text-slate-600 hover:bg-slate-100"
                    }`}
                  >
                    {c.label}
                  </Link>
                ))}
              </div>
            )}
            </div>
          );
        })}
      </nav>

      <div className="mt-auto pt-4">
        <button
          type="button"
          onClick={toggle}
          title={collapsed ? "Expand navigation" : "Collapse navigation"}
          aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
          className={`mb-2 flex w-full items-center rounded-md py-1.5 text-xs text-slate-400 hover:bg-slate-100 hover:text-slate-600 ${
            collapsed ? "justify-center" : "gap-2 px-3"
          }`}
        >
          <span className="text-base leading-none">{collapsed ? "»" : "«"}</span>
          {!collapsed && <span>Collapse</span>}
        </button>
        {!collapsed && <div className="border-t border-slate-200 pt-3">{footer}</div>}
      </div>
    </aside>
  );
}
