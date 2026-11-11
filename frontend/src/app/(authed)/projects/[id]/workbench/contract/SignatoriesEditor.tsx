"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { Button } from "@/components/ui/button";
import type { Signatory } from "@/lib/workbench";
import { setSignatoriesAction } from "./actions";

const inputCls =
  "w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500";

/** Edit the contract's signatories — name + title per row, add/remove, reorder
 *  is implicit (top-to-bottom). Saved to the contract and rendered into the PDF
 *  signature block (two per row). */
export function SignatoriesEditor({
  projectId,
  contractId,
  initial,
  ownerNameDefault,
}: {
  projectId: string;
  contractId: string;
  initial: Signatory[];
  ownerNameDefault: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Seed with a sensible default when the contract has no signatories yet:
  // the owner (from the client record) + the BRIO contractor block.
  const seed: Signatory[] =
    initial.length > 0
      ? initial
      : [
          { name: ownerNameDefault, title: "Owner" },
          {
            name: "BRIO Construction Inc.",
            title: "Contractor · Per: Amir Omidvar, Authorized Signatory",
          },
        ];
  const [rows, setRows] = useState<Signatory[]>(seed);

  const edit = (i: number, patch: Partial<Signatory>) =>
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const remove = (i: number) => setRows((prev) => prev.filter((_, j) => j !== i));
  const add = () => setRows((prev) => [...prev, { name: "", title: "Owner" }]);

  const save = () => {
    setErr(null);
    setSaved(false);
    startTransition(async () => {
      try {
        const clean = rows
          .map((r) => ({ name: r.name.trim(), title: r.title.trim() }))
          .filter((r) => r.name || r.title);
        await setSignatoriesAction(projectId, contractId, clean);
        setRows(clean.length ? clean : seed);
        setSaved(true);
        router.refresh();
      } catch (e) {
        setErr(String(e));
      }
    });
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        These appear as signature blocks in the contract PDF (two per row). Add a
        row per registered owner; keep the BRIO contractor line last.
      </p>

      <div className="space-y-2">
        {rows.map((r, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              value={r.name}
              onChange={(e) => edit(i, { name: e.target.value })}
              placeholder="Full legal name"
              className={inputCls}
            />
            <input
              value={r.title}
              onChange={(e) => edit(i, { title: e.target.value })}
              placeholder="Title (e.g., Owner)"
              className={inputCls}
            />
            <button
              type="button"
              onClick={() => remove(i)}
              title="Remove signatory"
              aria-label="Remove signatory"
              className="text-slate-300 hover:text-red-600 text-sm px-1"
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={add}
          className="px-2.5 py-1 rounded border border-slate-300 text-xs font-medium hover:bg-slate-50"
        >
          + Add signatory
        </button>
        <Button onClick={save} disabled={pending}>
          {pending ? "Saving…" : "Save signatories"}
        </Button>
        {saved && <span className="text-xs text-emerald-700">Saved.</span>}
        {err && <span className="text-xs text-red-600">{err}</span>}
      </div>
    </div>
  );
}
