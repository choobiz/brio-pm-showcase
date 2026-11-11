import Link from "next/link";
import { getEstimateVersion } from "@/lib/workbench";
import { fmtMoney } from "@/lib/fmt";
import { diffPayloads } from "@/lib/estimate-diff";
import type { EstimatePayload } from "@/lib/estimate-rollup";

/**
 * Side-by-side diff between two estimate versions.
 *
 * Routing convention: [vid] is the "right" (newer/current) version,
 * [otherVid] is the "left" (older/baseline) version. URLs read as
 * "vid diffed against otherVid". Numbers in the rollup card are shown
 * as right - left (positive = increase since left).
 */
export default async function EstimateDiffPage({
  params,
}: {
  params: Promise<{ id: string; vid: string; otherVid: string }>;
}) {
  const { id, vid, otherVid } = await params;

  // Fetch both in parallel
  const [rightV, leftV] = await Promise.all([
    getEstimateVersion(id, vid),
    getEstimateVersion(id, otherVid),
  ]);

  const right = rightV.payload as unknown as EstimatePayload;
  const left = leftV.payload as unknown as EstimatePayload;
  const d = diffPayloads(left, right);

  const cardBase = "rounded border p-3 text-sm";
  const swing = (n: number) =>
    n > 0 ? "text-emerald-700" : n < 0 ? "text-red-700" : "text-slate-500";
  const swingBg = (k: string) =>
    k === "added"
      ? "bg-emerald-50 border-emerald-200"
      : k === "removed"
        ? "bg-red-50 border-red-200"
        : k === "changed"
          ? "bg-amber-50 border-amber-200"
          : "bg-white border-slate-200";

  return (
    <div className="space-y-5">
      <header className="flex items-baseline justify-between flex-wrap gap-3">
        <div>
          <Link
            href={`/projects/${id}/estimate/${vid}`}
            className="text-sm text-blue-600 hover:underline"
          >
            ← Back to {rightV.reference}
          </Link>
          <h1 className="text-xl font-semibold mt-1">Diff</h1>
          <p className="text-sm text-slate-600 mt-0.5">
            Comparing <strong>{leftV.reference}</strong> (v{leftV.version_number}) →{" "}
            <strong>{rightV.reference}</strong> (v{rightV.version_number})
          </p>
        </div>
      </header>

      {/* Rollup deltas */}
      <section className={`${cardBase} border-blue-200 bg-blue-50/40`}>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div>
            <div className="text-[10px] uppercase text-slate-500">
              Left pretax ({leftV.reference})
            </div>
            <div className="text-lg font-semibold tabular-nums">
              {fmtMoney(d.leftPretax)}
            </div>
            <div className="text-[10px] text-slate-500">
              Mgmt Fee {d.leftOverhead.toFixed(2)}%
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase text-slate-500">
              Right pretax ({rightV.reference})
            </div>
            <div className="text-lg font-semibold tabular-nums">
              {fmtMoney(d.rightPretax)}
            </div>
            <div className="text-[10px] text-slate-500">
              Mgmt Fee {d.rightOverhead.toFixed(2)}%
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase text-slate-500">Δ pretax</div>
            <div
              className={`text-lg font-semibold tabular-nums ${swing(d.pretaxDelta)}`}
            >
              {d.pretaxDelta > 0 ? "+" : ""}
              {fmtMoney(d.pretaxDelta)}
            </div>
            <div className="text-[10px] text-slate-500">
              {d.pretaxDelta === 0
                ? "no change"
                : `${((d.pretaxDelta / Math.max(d.leftPretax, 1)) * 100).toFixed(1)}%`}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase text-slate-500">
              Categories changed
            </div>
            <div className="text-lg font-semibold tabular-nums">
              {d.categories.filter((c) => c.kind !== "unchanged").length} /{" "}
              {d.categories.length}
            </div>
          </div>
        </div>
      </section>

      {/* Category-level deltas */}
      <section>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-700 mb-2">
          Category deltas
        </h2>
        <table className="w-full text-sm border border-slate-200">
          <thead className="bg-slate-50 text-left">
            <tr>
              <th className="px-3 py-2">Category</th>
              <th className="px-3 py-2 text-right">Left total</th>
              <th className="px-3 py-2 text-right">Right total</th>
              <th className="px-3 py-2 text-right">Δ</th>
              <th className="px-3 py-2 text-right">Items changed</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {d.categories.map((cd) => {
              const changed = cd.items.filter((i) => i.kind !== "unchanged").length;
              return (
                <tr key={cd.name} className="border-t border-slate-100">
                  <td className="px-3 py-2 font-medium">
                    {cd.name}
                    {cd.kind === "added" && (
                      <span className="ml-2 text-[10px] font-bold uppercase text-emerald-700">
                        + new
                      </span>
                    )}
                    {cd.kind === "removed" && (
                      <span className="ml-2 text-[10px] font-bold uppercase text-red-700">
                        − removed
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                    {fmtMoney(cd.leftTotal)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                    {fmtMoney(cd.rightTotal)}
                  </td>
                  <td
                    className={`px-3 py-2 text-right tabular-nums font-medium ${swing(cd.delta)}`}
                  >
                    {cd.delta > 0 ? "+" : ""}
                    {fmtMoney(cd.delta)}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-500">
                    {changed} / {cd.items.length}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {changed > 0 && (
                      <a
                        href={`#cat-${cd.name.replace(/\W+/g, "-")}`}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        details ↓
                      </a>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {/* Item-level details, only for changed categories */}
      <section className="space-y-4">
        {d.categories
          .filter((cd) => cd.kind !== "unchanged")
          .map((cd) => (
            <div
              key={cd.name}
              id={`cat-${cd.name.replace(/\W+/g, "-")}`}
              className={cardBase + " " + swingBg(cd.kind)}
            >
              <h3 className="font-medium text-sm mb-2">{cd.name}</h3>
              <table className="w-full text-xs">
                <thead className="text-slate-600 bg-white/50">
                  <tr>
                    <th className="text-left px-2 py-1 font-medium w-12">#</th>
                    <th className="text-left px-2 py-1 font-medium">Item</th>
                    <th className="text-right px-2 py-1 font-medium w-24">Left $</th>
                    <th className="text-right px-2 py-1 font-medium w-24">Right $</th>
                    <th className="text-right px-2 py-1 font-medium w-24">Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {cd.items.map((it, k) => {
                    const left = "left" in it ? it.left : undefined;
                    const right = "right" in it ? it.right : undefined;
                    const leftTotal =
                      left ? (Number(left.labor) || 0) + (Number(left.materials) || 0) : 0;
                    const rightTotal =
                      right ? (Number(right.labor) || 0) + (Number(right.materials) || 0) : 0;
                    const itemDelta = rightTotal - leftTotal;
                    return (
                      <tr
                        key={k}
                        className={`border-t border-slate-100/50 ${
                          it.kind === "added"
                            ? "bg-emerald-100/50"
                            : it.kind === "removed"
                              ? "bg-red-100/50 line-through opacity-75"
                              : it.kind === "changed"
                                ? "bg-amber-100/40"
                                : ""
                        }`}
                      >
                        <td className="px-2 py-1 text-slate-500">{it.idx + 1}</td>
                        <td className="px-2 py-1">
                          {right?.name || left?.name}
                          {it.kind === "added" && (
                            <span className="ml-2 text-[10px] font-bold uppercase text-emerald-700">
                              + added
                            </span>
                          )}
                          {it.kind === "removed" && (
                            <span className="ml-2 text-[10px] font-bold uppercase text-red-700">
                              − removed
                            </span>
                          )}
                          {it.kind === "changed" && left && right && left.name !== right.name && (
                            <div className="text-[10px] text-slate-500 mt-0.5">
                              was: {left.name}
                            </div>
                          )}
                        </td>
                        <td className="px-2 py-1 text-right tabular-nums text-slate-700">
                          {left ? fmtMoney(leftTotal) : "—"}
                        </td>
                        <td className="px-2 py-1 text-right tabular-nums text-slate-700">
                          {right ? fmtMoney(rightTotal) : "—"}
                        </td>
                        <td
                          className={`px-2 py-1 text-right tabular-nums font-medium ${swing(itemDelta)}`}
                        >
                          {itemDelta === 0
                            ? ""
                            : (itemDelta > 0 ? "+" : "") + fmtMoney(itemDelta)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ))}
      </section>
    </div>
  );
}
