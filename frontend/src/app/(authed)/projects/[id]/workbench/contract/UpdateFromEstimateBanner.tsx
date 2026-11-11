"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { Button } from "@/components/ui/button";
import { regenerateDownstreamAction } from "../actions";

function money(n: number): string {
  return n.toLocaleString("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 });
}

/**
 * Shown when the contract's stored value no longer matches the active estimate
 * (e.g. the estimate was edited in place). One click recomputes the contract
 * (and SOV/cash flow) from the active estimate. Draft contracts only — a
 * signed contract's value is fixed and should not be silently changed.
 */
export function UpdateFromEstimateBanner({
  projectId,
  storedInclGst,
  activeInclGst,
  locked,
}: {
  projectId: string;
  storedInclGst: number;
  activeInclGst: number;
  locked: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [err, setErr] = useState<string | null>(null);

  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="text-sm text-amber-900">
          <div className="font-semibold">Contract value is out of date</div>
          <div className="mt-0.5">
            This contract shows <b>{money(storedInclGst)}</b>, but the active estimate
            now totals <b>{money(activeInclGst)}</b> (incl. GST). The estimate changed
            after this contract was generated.
          </div>
          {locked && (
            <div className="mt-1 text-xs text-amber-800">
              This contract is signed, so its value changes only by change order.
            </div>
          )}
        </div>
        {!locked && <div className="shrink-0">
          <Button
            onClick={() => {
              setErr(null);
              startTransition(async () => {
                try {
                  await regenerateDownstreamAction(projectId);
                  router.refresh();
                } catch (e) {
                  setErr(String(e));
                }
              });
            }}
            disabled={pending}
          >
            {pending ? "Updating…" : "Update from active estimate"}
          </Button>
        </div>}
      </div>
      {err && <div className="mt-2 text-xs text-red-600">{err}</div>}
    </div>
  );
}
