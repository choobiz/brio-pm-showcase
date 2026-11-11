"use client";

import { useEffect, useState } from "react";
import { getPackageStatus, markPackageSent, type PackageStatus } from "@/lib/packageStatus";

function fmt(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/**
 * Warns when the client package is stale — i.e. package-affecting data
 * (schedule dates, tasks, contract amount, payment terms, discussion,
 * selections) changed since the package was last sent to the client.
 */
export function PackageStatusBanner({ projectId }: { projectId: string }) {
  const [st, setSt] = useState<PackageStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () =>
    getPackageStatus(projectId)
      .then(setSt)
      .catch(() => setSt(null));

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  if (!st) return null;

  async function onMark() {
    setBusy(true);
    try {
      await markPackageSent(projectId);
      await load();
    } finally {
      setBusy(false);
    }
  }

  // Never sent — subtle neutral note.
  if (!st.has_baseline) {
    return (
      <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500">
        Client package not yet sent. Downloading the full package marks the current
        data as the sent baseline.
      </div>
    );
  }

  // Up to date.
  if (!st.stale) {
    return (
      <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
        ✓ Client package up to date{st.marked_at ? ` — sent ${fmt(st.marked_at)}` : ""}.
      </div>
    );
  }

  // Stale — name what changed + offer regenerate / mark-as-sent.
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2.5 text-sm text-amber-900">
      <div className="font-semibold">
        ⚠ Client package is out of date
      </div>
      <div className="mt-0.5 text-xs">
        Changed since last sent{st.marked_at ? ` (${fmt(st.marked_at)})` : ""}:{" "}
        <b>{st.changed_labels.join(", ")}</b>. Regenerate and resend to the client.
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <a
          href={`/api/workbench/client-package-pdf/${projectId}?doc=merged`}
          target="_blank"
          rel="noopener noreferrer"
          className="rounded px-2.5 py-1 text-xs font-semibold text-white hover:opacity-90"
          style={{ backgroundColor: "#1D3146" }}
        >
          ⤓ Download updated package
        </a>
        <button
          type="button"
          onClick={onMark}
          disabled={busy}
          className="rounded border border-amber-400 bg-white px-2.5 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50"
        >
          {busy ? "Marking…" : "Mark as sent"}
        </button>
      </div>
    </div>
  );
}
