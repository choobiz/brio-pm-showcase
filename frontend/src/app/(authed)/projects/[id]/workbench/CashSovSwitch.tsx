import Link from "next/link";

/** Money › "Cash flow & SOV" (round 2, Amir 2026-10-23): two pages, one
 *  sub-tab, switched here. Both are built by Contract › Billing plan. */
export function CashSovSwitch({ projectId, on }: { projectId: string; on: "cashflow" | "sov" }) {
  const cls = (me: boolean) =>
    `px-3 py-1.5 text-xs font-medium ${me ? "bg-slate-900 text-white" : "bg-white text-slate-700 hover:bg-slate-100"}`;
  return (
    <div className="inline-flex overflow-hidden rounded border border-slate-300" role="tablist">
      <Link href={`/projects/${projectId}/workbench/cashflow`} className={cls(on === "cashflow")}
        aria-current={on === "cashflow" ? "page" : undefined}>Cash flow</Link>
      <Link href={`/projects/${projectId}/workbench/sov`} className={`${cls(on === "sov")} border-l border-slate-300`}
        aria-current={on === "sov" ? "page" : undefined}>Schedule of values</Link>
    </div>
  );
}

/** Shown when nothing has been built yet. */
export function NotBuiltYet({ projectId, what }: { projectId: string; what: string }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-700">
      No {what} for this project yet. It&apos;s built when you save the billing plan on{" "}
      <Link href={`/projects/${projectId}/workbench/contract`} className="text-blue-700">Contract</Link>.
    </div>
  );
}
