"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { Button } from "@/components/ui/button";
import type { ContractStatus } from "@/lib/workbench";
import { setContractStatusAction } from "./actions";

const STEPS: { key: ContractStatus; label: string }[] = [
  { key: "draft", label: "Draft" },
  { key: "issued", label: "Issued" },
  { key: "signed", label: "Signed" },
  { key: "executed", label: "Executed" },
];

const RANK: Record<ContractStatus, number> = {
  draft: 0,
  issued: 1,
  signed: 2,
  executed: 3,
};

/** Lifecycle controls for a contract: advance draft → issued → signed →
 *  executed, capturing the owner's name + date at signing. Office-only
 *  (the backend gates it); this just drives the UI. */
export function ContractStatusControls({
  projectId,
  contractId,
  status,
  ownerNameDefault,
}: {
  projectId: string;
  contractId: string;
  status: ContractStatus;
  ownerNameDefault: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [err, setErr] = useState<string | null>(null);
  const [showSign, setShowSign] = useState(false);
  const [ownerName, setOwnerName] = useState(ownerNameDefault);
  const today = new Date().toISOString().slice(0, 10);
  const [signedDate, setSignedDate] = useState(today);

  const rank = RANK[status];

  const run = (body: Parameters<typeof setContractStatusAction>[2]) => {
    setErr(null);
    startTransition(async () => {
      try {
        await setContractStatusAction(projectId, contractId, body);
        setShowSign(false);
        router.refresh();
      } catch (e) {
        setErr(String(e));
      }
    });
  };

  return (
    <div className="space-y-4">
      {/* Stepper */}
      <ol className="flex items-center gap-2 text-xs">
        {STEPS.map((s, i) => {
          const done = i < rank;
          const current = i === rank;
          return (
            <li key={s.key} className="flex items-center gap-2">
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium ${
                  current
                    ? "bg-[var(--color-brio-primary)] text-white"
                    : done
                      ? "bg-emerald-100 text-emerald-800"
                      : "bg-slate-100 text-slate-500"
                }`}
              >
                {done ? "✓" : i + 1} {s.label}
              </span>
              {i < STEPS.length - 1 && (
                <span className={done ? "text-emerald-400" : "text-slate-300"}>→</span>
              )}
            </li>
          );
        })}
      </ol>

      {/* Primary actions */}
      <div className="flex flex-wrap items-center gap-2">
        {status === "draft" && (
          <Button onClick={() => run({ status: "issued" })} disabled={pending}>
            {pending ? "Working…" : "Mark as Issued (sent to client)"}
          </Button>
        )}

        {status === "issued" && !showSign && (
          <Button onClick={() => setShowSign(true)} disabled={pending}>
            Mark as Signed…
          </Button>
        )}

        {status === "signed" && (
          <Button
            onClick={() =>
              run({ status: "executed" })
            }
            disabled={pending}
          >
            {pending ? "Working…" : "Mark as Executed (BRIO countersigned)"}
          </Button>
        )}

        {status === "executed" && (
          <span className="text-sm text-emerald-700 font-medium">
            ✓ Contract fully executed.
          </span>
        )}

        {/* Revert one step — small, unobtrusive */}
        {rank > 0 && !showSign && (
          <button
            type="button"
            onClick={() => run({ status: STEPS[rank - 1].key })}
            disabled={pending}
            className="text-xs text-slate-400 hover:text-slate-600 underline"
          >
            Revert to {STEPS[rank - 1].label}
          </button>
        )}
      </div>

      {/* Signing capture form */}
      {showSign && (
        <div className="rounded border border-slate-200 bg-slate-50 p-3 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="text-sm">
              <span className="block text-slate-600 mb-1">Signed by (owner)</span>
              <input
                value={ownerName}
                onChange={(e) => setOwnerName(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
                placeholder="Owner legal name"
              />
            </label>
            <label className="text-sm">
              <span className="block text-slate-600 mb-1">Date signed</span>
              <input
                type="date"
                value={signedDate}
                onChange={(e) => setSignedDate(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              />
            </label>
          </div>
          <div className="flex gap-2">
            <Button
              onClick={() =>
                run({
                  status: "signed",
                  signed_by_owner: ownerName.trim() || undefined,
                  signed_date: signedDate || undefined,
                })
              }
              disabled={pending}
            >
              {pending ? "Saving…" : "Confirm signed"}
            </Button>
            <Button variant="ghost" onClick={() => setShowSign(false)} disabled={pending}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {err && <div className="text-xs text-red-600">{err}</div>}
    </div>
  );
}
