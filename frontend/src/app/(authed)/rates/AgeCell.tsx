"use client";

import type { Drift } from "@/lib/rates";

const month = (d: string) =>
  new Date(`${d}T12:00:00`).toLocaleDateString("en-CA", { month: "short", year: "numeric" });

/** A price's date plus how far StatCan's Vancouver index has moved since (phase 2). */
export function AgeCell({ asOf, drift }: { asOf: string | null | undefined; drift?: Drift | null }) {
  if (!asOf) return <span className="text-xs text-orange-800">⚠ no price date</span>;
  const pct = drift?.pct;
  return (
    <span className={`whitespace-nowrap text-xs ${drift?.stale ? "text-orange-800" : "text-slate-600"}`}>
      {month(asOf)}
      {pct != null && pct !== 0 ? ` · ${pct > 0 ? "+" : ""}${pct}% since` : ""}
      {drift?.stale ? " ⚠" : ""}
    </span>
  );
}
