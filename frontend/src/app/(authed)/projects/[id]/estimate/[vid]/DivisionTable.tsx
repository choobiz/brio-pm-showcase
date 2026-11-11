"use client";

import Link from "next/link";
import { Fragment, useMemo, useState } from "react";

import { Box, Label } from "@/components/kit";
import { fmtMoney } from "@/lib/fmt";
import type { DrawingLink, EstimateTotals } from "@/lib/workbench";
import { clientSafe } from "@/lib/clientRedact";
import { rateSourceWords } from "@/lib/rateSource";

type RateRef = {
  code: string;
  kind: string;
  name?: string;
  unit: string;
  rate: number;
  source?: string;
  confidence?: string | null;
  source_ref?: string | null;
  as_of?: string | null;
};

/** How a line is tied to the rate library (phase 3, services/rate_links.py). */
export type RateLink = {
  status: "linked" | "refused" | "unlinked";
  basis?: string;
  reason?: string;
  rate?: RateRef | null;
  labour?: RateRef | null;
  qty_source?: string;
  library_total?: number;
  ai_total?: number;
  gap_pct?: number | null;
  flagged?: boolean;
  writeback?: { status: "written" | "job_only" | "pending"; reason?: string; by?: string; at?: string };
};

export type Line = {
  name: string;
  qty?: number | string;
  unit?: string;
  unitCost?: number;
  laborHours?: number;
  labor?: number;
  materials?: number;
  contingency?: boolean;
  basis?: string;
  rate_link?: RateLink;
};

/** Plain words for where a rate came from. */
function sourceLabel(r: RateRef): string {
  return rateSourceWords(r.source, r.confidence, r.source_ref);
}

const month = (d?: string | null) =>
  d ? new Date(`${d}T12:00:00`).toLocaleDateString("en-CA", { month: "short", year: "numeric" }) : "";

/** The small row under a line: which library rate priced it, or why none did. */
function LinkNote({ link }: { link: RateLink }) {
  const wb = link.writeback;
  const wbText =
    wb?.status === "written"
      ? " · rate updated from this estimate"
      : wb?.status === "job_only"
        ? ` · kept to this job: ${wb.reason}`
        : wb?.status === "pending"
          ? " · fix not yet written to the library"
          : "";
  if (link.status === "linked") {
    const r = link.rate ?? link.labour;
    if (!r) return null;
    const gap = link.gap_pct;
    return (
      <span>
        ↳ <span className="font-mono">{r.code}</span> · {sourceLabel(r)}
        {r.as_of ? ` · ${month(r.as_of)}` : ""}
        {link.labour && link.rate ? ` · labour ${link.labour.code}` : ""}
        {link.qty_source?.startsWith("takeoff:") ? " · qty from signed takeoff" : ""}
        {link.flagged && gap != null ? (
          <span className="text-orange-800">
            {" "}
            · AI had {fmtMoney(link.ai_total ?? null)} ({gap > 0 ? "+" : ""}
            {gap}%) ⚠
          </span>
        ) : null}
        {wbText}{" "}
        <Link href={`/rates?code=${encodeURIComponent(r.code)}`} className="text-blue-700" onClick={(e) => e.stopPropagation()}>
          open rate
        </Link>
      </span>
    );
  }
  if (link.status === "refused") {
    return <span className="text-orange-800">↳ allowance · link refused: {link.reason}</span>;
  }
  return <span>↳ {link.basis ?? "allowance"} · no library rate</span>;
}

export type Category = {
  name: string;
  description?: string;
  subtotalRaw?: number;
  contingencyAmount?: number;
  contingencyPct?: number;
  total?: number;
  optional?: boolean;
  items?: Line[];
  rate_provenance?: Record<string, unknown> | null;
  rate_benchmark?: Record<string, unknown> | null;
};

type Source = "measured" | "allowance" | null;

/** "04 Rough Carpentry" → "04". Division labels lead with their number. */
const code = (name: string) => name.trim().match(/^(\d{2})\b/)?.[1] ?? null;

function sourceOf(cat: Category, measured: string[], allowance: string[]): Source {
  const c = code(cat.name);
  const has = (list: string[]) =>
    list.some((d) => d.trim() === cat.name.trim() || (c !== null && code(d) === c));
  if (has(measured)) return "measured";
  if (has(allowance)) return "allowance";
  return null;
}

const sum = (xs: (number | undefined)[]) => xs.reduce<number>((s, x) => s + (Number(x) || 0), 0);

/**
 * The estimate by division: labour, materials, total, and whether the division
 * was measured from drawings or priced as an allowance. Expand a division for
 * its lines; click a line for where its quantity and rate came from.
 *
 * "Our view" shows overhead and profit as their own lines. "Client's view" folds
 * them into the total, as the client documents do.
 */
export function DivisionTable({
  projectId,
  categories,
  totals,
  measured,
  allowance,
  links,
  clientView,
  assumptions,
  exclusions,
}: {
  projectId: string;
  categories: Category[];
  totals: EstimateTotals | null;
  measured: string[];
  allowance: string[];
  links: DrawingLink[];
  clientView: boolean;
  assumptions: string[];
  exclusions: string[];
}) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [picked, setPicked] = useState<{ cat: number; line: number } | null>(null);

  const base = categories.map((c, i) => ({ c, i })).filter(({ c }) => !c.optional);
  const optional = categories.map((c, i) => ({ c, i })).filter(({ c }) => c.optional);

  const pickedCat = picked ? categories[picked.cat] : null;
  const pickedLine = picked && pickedCat ? (pickedCat.items ?? [])[picked.line] : null;
  const pickedLinks = useMemo(
    () =>
      picked && pickedCat
        ? links.filter((l) => l.trade_name === pickedCat.name && l.line_item_index === picked.line)
        : [],
    [picked, pickedCat, links],
  );

  // Client's view: each division carries its share of overhead and profit, so
  // the rows still add up to the subtotal the client sees (BRIO rule: client
  // documents show Subtotal → GST → Total, never the margin).
  const factor = clientView && totals && totals.subtotal ? totals.before_gst / totals.subtotal : 1;
  // The client's view reads the notes a client would: internal wording out.
  if (clientView) {
    assumptions = clientSafe(assumptions);
    exclusions = clientSafe(exclusions);
  }
  const money = (n: number | null | undefined) => (n == null ? fmtMoney(null) : fmtMoney(Math.round(n * factor)));

  const toggle = (i: number) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(i)) n.delete(i);
      else n.add(i);
      return n;
    });

  const rows = (list: { c: Category; i: number }[]) =>
    list.map(({ c, i }) => {
      const items = (c.items ?? []).filter((it) => !it.contingency);
      const src = sourceOf(c, measured, allowance);
      const isOpen = open.has(i);
      return (
        <Fragment key={i}>
          <tr className="border-b border-slate-100">
            <td className="px-4 py-2.5">
              <button
                type="button"
                onClick={() => toggle(i)}
                disabled={!items.length}
                className="text-left font-semibold disabled:cursor-default"
                aria-expanded={isOpen}
              >
                {items.length ? (isOpen ? "▾ " : "▸ ") : ""}
                {c.name}
              </button>
            </td>
            <td className="px-3 py-2.5 text-right tabular-nums">{items.length && !clientView ? fmtMoney(sum(items.map((x) => x.labor))) : ""}</td>
            <td className="px-3 py-2.5 text-right tabular-nums">{items.length && !clientView ? fmtMoney(sum(items.map((x) => x.materials))) : ""}</td>
            <td className="px-3 py-2.5 text-right tabular-nums">{money(c.total)}</td>
            <td className="px-3 py-2.5">
              {src && (
                <span
                  className={`rounded border px-1.5 py-0.5 text-[11px] ${
                    src === "allowance" ? "border-orange-700 text-orange-800" : "border-slate-500 text-slate-700"
                  }`}
                >
                  {src}
                </span>
              )}
            </td>
          </tr>
          {isOpen &&
            (c.items ?? []).map((it, j) =>
              it.contingency && clientView ? null : it.contingency ? (
                <tr key={`${i}-${j}`} className="border-b border-slate-100 bg-slate-50 text-[13px] text-slate-600">
                  <td className="py-2 pl-9 pr-3">{it.name}</td>
                  <td />
                  <td className="px-3 py-2 text-right tabular-nums">{fmtMoney(it.materials ?? null)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{fmtMoney(sum([it.labor, it.materials]))}</td>
                  <td />
                </tr>
              ) : (
                <tr
                  key={`${i}-${j}`}
                  onClick={() => setPicked({ cat: i, line: j })}
                  className={`cursor-pointer border-b border-slate-100 text-[13px] ${
                    picked?.cat === i && picked?.line === j ? "bg-blue-50" : "bg-slate-50 hover:bg-slate-100"
                  }`}
                >
                  <td className="py-2 pl-9 pr-3">
                    {it.name}
                    {it.qty != null && it.qty !== "" ? <span className="text-slate-500"> · {it.qty}</span> : null}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{clientView ? "" : fmtMoney(it.labor ?? null)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{clientView ? "" : fmtMoney(it.materials ?? null)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{clientView ? "" : fmtMoney(sum([it.labor, it.materials]))}</td>
                  <td className="px-3 py-2 text-xs text-slate-600">
                    {links.some((l) => l.trade_name === c.name && l.line_item_index === j) ? "drawing" : ""}
                  </td>
                </tr>
              ),
            ).flatMap((row, j) => {
              const it = (c.items ?? [])[j];
              if (!row || clientView || !it || it.contingency || !it.rate_link) return [row];
              return [
                row,
                <tr key={`${i}-${j}-link`} className="border-b border-slate-100 bg-slate-50 text-[12px] text-slate-600">
                  <td colSpan={5} className="pb-1.5 pl-12 pr-3 pt-0">
                    <LinkNote link={it.rate_link} />
                  </td>
                </tr>,
              ];
            })}
        </Fragment>
      );
    });

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
      <Box className="overflow-x-auto p-0">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
            <tr className="border-b border-slate-300">
              <th className="px-4 py-2">Division</th>
              <th className="px-3 py-2 text-right">Labour</th>
              <th className="px-3 py-2 text-right">Materials</th>
              <th className="px-3 py-2 text-right">Total</th>
              <th className="px-3 py-2">Source</th>
            </tr>
          </thead>
          <tbody>
            {rows(base)}
            {totals && (
              <>
                <tr className="border-b border-slate-200">
                  <td className="px-4 py-2.5">Subtotal</td>
                  <td colSpan={2} />
                  <td className="px-3 py-2.5 text-right tabular-nums">
                    {fmtMoney(clientView ? totals.before_gst : totals.subtotal)}
                  </td>
                  <td />
                </tr>
                {!clientView && (
                  <tr className="border-b border-slate-200 text-slate-600">
                    <td className="px-4 py-2.5">
                      Overhead {totals.overhead_pct}% and profit {totals.profit_pct}%{" "}
                      <span className="rounded border border-slate-400 px-1 text-[11px]">our view only</span>
                    </td>
                    <td colSpan={2} />
                    <td className="px-3 py-2.5 text-right tabular-nums">{fmtMoney(totals.overhead + totals.profit)}</td>
                    <td />
                  </tr>
                )}
                <tr className="border-b border-slate-200">
                  <td className="px-4 py-2.5">GST 5%</td>
                  <td colSpan={2} />
                  <td className="px-3 py-2.5 text-right tabular-nums">{fmtMoney(totals.gst)}</td>
                  <td />
                </tr>
                <tr>
                  <td className="px-4 py-2.5 font-bold">Total</td>
                  <td colSpan={2} />
                  <td className="px-3 py-2.5 text-right font-bold tabular-nums">{fmtMoney(totals.with_gst)}</td>
                  <td />
                </tr>
              </>
            )}
            {optional.length > 0 && (
              <>
                <tr>
                  <td colSpan={5} className="px-4 pb-1 pt-5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Optional add-ons (not in the total)
                  </td>
                </tr>
                {rows(optional)}
              </>
            )}
          </tbody>
        </table>
      </Box>

      <div className="space-y-3">
        <Box className="space-y-2 p-4 text-[13px]">
          <Label>Why this number</Label>
          {!pickedLine || !pickedCat ? (
            <p className="m-0 text-slate-600">Open a division and click a line to see where its quantity and rate came from.</p>
          ) : (
            <>
              <div className="font-semibold">{pickedLine.name}</div>
              <div className="text-slate-700">
                {pickedLine.qty != null && pickedLine.qty !== "" ? <>Quantity {String(pickedLine.qty)}</> : "No quantity given"}
                {` · labour ${fmtMoney(pickedLine.labor ?? 0)}, materials ${fmtMoney(pickedLine.materials ?? 0)}`}
                {pickedLine.laborHours ? ` · ${pickedLine.laborHours} labour hours` : ""}
                {/* unitCost is not shown: the model's unit cost often does not
                    multiply out to its labour + materials (400 × $5 beside
                    $720 on 1234 v6), and quoting it reads as a contradiction. */}
              </div>
              <div className="border-t border-slate-200 pt-2">
                <b>Quantity:</b>{" "}
                {pickedLinks.length ? (
                  pickedLinks.map((l) => (
                    <span key={l.id} className="block">
                      from {l.filename}
                      {l.sheet_number ? `, sheet ${l.sheet_number}` : ""}
                      {l.page_number ? `, page ${l.page_number}` : ""}
                      {l.verified ? " (checked)" : " (not checked)"}
                    </span>
                  ))
                ) : (
                  <span className="text-slate-600">
                    no drawing is linked to this line. It is the AI&apos;s reading of the scope; measured quantities are
                    used for the division as a whole (see{" "}
                    <Link href={`/projects/${projectId}/takeoff`} className="text-blue-700">
                      Takeoff
                    </Link>
                    ).
                  </span>
                )}
              </div>
              <div className="border-t border-slate-200 pt-2">
                <b>Rate:</b>{" "}
                {pickedLine.rate_link?.status === "linked" && (pickedLine.rate_link.rate || pickedLine.rate_link.labour) ? (
                  <span>
                    priced from the rate library:{" "}
                    {[pickedLine.rate_link.rate, pickedLine.rate_link.labour]
                      .filter((r): r is RateRef => !!r)
                      .map((r) => `${r.name ?? r.code} (${r.code}) at ${fmtMoney(r.rate)}/${r.unit}, ${sourceLabel(r)}`)
                      .join("; ")}
                    .
                    {pickedLine.rate_link.ai_total != null
                      ? ` The AI had priced it at ${fmtMoney(pickedLine.rate_link.ai_total)}.`
                      : ""}
                  </span>
                ) : pickedLine.rate_link ? (
                  <span className="text-slate-600">
                    {pickedLine.rate_link.status === "refused"
                      ? `the AI cited a rate that didn't fit (${pickedLine.rate_link.reason}), so this is its own allowance.`
                      : `priced by the AI as ${pickedLine.rate_link.basis ?? "an allowance"}; the library has no rate for it yet.`}
                  </span>
                ) : pickedCat.rate_provenance ? (
                  <span>
                    rate library ({String(pickedCat.rate_provenance.source_type ?? "")}
                    {pickedCat.rate_provenance.source_ref ? `, ${String(pickedCat.rate_provenance.source_ref)}` : ""}) for{" "}
                    the whole division.
                  </span>
                ) : pickedCat.rate_benchmark ? (
                  <span>
                    priced by the AI; the rate library was used as a check for this division, not to set the price.
                  </span>
                ) : (
                  <span className="text-slate-600">priced by the AI; no rate-library entry is recorded for this division.</span>
                )}
              </div>
              {!pickedLine.rate_link && (
                <p className="m-0 border-t border-slate-200 pt-2 text-xs text-slate-500">
                  This estimate predates line-by-line rate links (16 Oct 2026): sources are per division.
                </p>
              )}
            </>
          )}
        </Box>

        {(assumptions.length > 0 || exclusions.length > 0) && (
          <Box className="space-y-2 p-4 text-[13px]">
            {assumptions.length > 0 && (
              <>
                <Label>Assumptions on this version</Label>
                <ul className="m-0 list-disc space-y-0.5 pl-4 text-slate-700">
                  {assumptions.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </>
            )}
            {exclusions.length > 0 && (
              <>
                <Label className="pt-1">Not included</Label>
                <ul className="m-0 list-disc space-y-0.5 pl-4 text-slate-700">
                  {exclusions.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </>
            )}
          </Box>
        )}
      </div>
    </div>
  );
}
