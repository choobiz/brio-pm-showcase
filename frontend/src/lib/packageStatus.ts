// Client-side helpers for the stale-package warning.

const BASE = "/api/backend";

export interface PackageStatus {
  has_baseline: boolean;
  stale: boolean;
  changed_sections: string[];
  changed_labels: string[];
  marked_at: string | null;
  marked_by?: string;
  details?: {
    schedule_dates?: { start: string; p50: string; p80: string };
    contract_total?: number | null;
    n_tasks?: number;
  };
}

export async function getPackageStatus(projectId: string): Promise<PackageStatus> {
  const r = await fetch(`${BASE}/projects/${projectId}/workbench/package-status`, {
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`package-status ${r.status}`);
  return r.json();
}

export async function markPackageSent(
  projectId: string,
): Promise<{ ok: boolean; marked_at: string }> {
  const r = await fetch(`${BASE}/projects/${projectId}/workbench/package-mark-sent`, {
    method: "POST",
  });
  if (!r.ok) throw new Error(`package-mark-sent ${r.status}`);
  return r.json();
}
