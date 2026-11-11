// The few building blocks the round-1 pages share (wireframes, 2026-10-10):
// a white box, a small uppercase section label, a tag. Kept deliberately small;
// a page that needs something more specific builds it locally.

import type { ReactNode } from "react";

export function Box({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-slate-300 bg-white p-5 ${className}`}>{children}</section>
  );
}

export function Label({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <h2 className={`m-0 text-xs font-semibold uppercase tracking-wider text-slate-600 ${className}`}>
      {children}
    </h2>
  );
}

const TONE = {
  plain: "border-slate-300 text-slate-600",
  warn: "border-orange-700 text-orange-800",
  ok: "border-green-700 text-green-800",
  strong: "border-slate-900 text-slate-900",
} as const;

export function Tag({ children, tone = "plain" }: { children: ReactNode; tone?: keyof typeof TONE }) {
  return (
    <span className={`whitespace-nowrap rounded border px-1.5 py-px font-mono text-[11px] ${TONE[tone]}`}>
      {children}
    </span>
  );
}
