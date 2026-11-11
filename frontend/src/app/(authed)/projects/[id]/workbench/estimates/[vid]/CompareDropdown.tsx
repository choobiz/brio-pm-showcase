"use client";

import Link from "next/link";
import { useState } from "react";

type Pick = { id: string; version_number: number; reference: string };

/**
 * Compare button — opens a small dropdown listing every other version
 * on this project. Picking one navigates to the diff route.
 *
 * Kept compact + native rather than fancy combobox because the version
 * list is short (typically 2–10 entries) and the action is rare.
 */
export function CompareDropdown({
  projectId,
  vid,
  options,
}: {
  projectId: string;
  vid: string;
  options: Pick[];
}) {
  const [open, setOpen] = useState(false);
  if (options.length === 0) return null;
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((s) => !s)}
        className="px-3 py-1.5 rounded border border-slate-300 text-xs font-medium hover:bg-slate-50"
      >
        ⇄ Compare with…
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 w-64 bg-white border border-slate-200 rounded shadow-lg text-xs">
          <div className="px-3 py-2 border-b border-slate-100 text-slate-500 uppercase tracking-wide font-semibold text-[10px]">
            Compare against
          </div>
          <ul className="max-h-72 overflow-y-auto">
            {options.map((o) => (
              <li key={o.id}>
                <Link
                  href={`/projects/${projectId}/workbench/estimates/${vid}/diff/${o.id}`}
                  className="block px-3 py-1.5 hover:bg-slate-50"
                  onClick={() => setOpen(false)}
                >
                  v{String(o.version_number).padStart(3, "0")} — {o.reference}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
