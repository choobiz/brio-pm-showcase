// The register — what needs a person's attention, as typed items.
//
// Read-only. Phase 1 writes nothing, so there is no mutation in this module and
// there should not be one added without the approval flow that Phase 3 brings.

// Transport, including how a failure is described, lives in workspaceApi.
import { workspaceGet } from "@/lib/workspaceApi";

const api = workspaceGet;

export type Severity = "blocking" | "attention";

export interface RegisterItem {
  key: string;
  severity: Severity;
  kind: string;
  project_id: string | null;
  project_name: string | null;
  title: string;
  /** The consequence in one short line. Always visible, kept scannable —
   *  the first build showed the full reasoning on every card and one sentence
   *  appeared four times on a single screen. */
  summary: string;
  /** The reasoning. Shown on demand, not by default. */
  why: string;
  count: number | null;
  since?: string | null;
  /** The records the item was derived from, so a reader can check it. */
  evidence: Record<string, unknown>;
  actions: { label: string; href: string }[];
}

export interface RegisterResponse {
  scope: "project" | "all-accessible";
  projects_in_scope: number;
  summary: {
    total: number;
    blocking: number;
    attention: number;
    projects_blocked: number;
  };
  items: RegisterItem[];
}

export function getRegister(opts?: { projectId?: string; severity?: Severity }) {
  const q = new URLSearchParams();
  if (opts?.projectId) q.set("project_id", opts.projectId);
  if (opts?.severity) q.set("severity", opts.severity);
  const qs = q.toString();
  return api<RegisterResponse>(`/register${qs ? `?${qs}` : ""}`);
}

/** The phrase the API returns wherever a link was never recorded. Compared
 *  against rather than re-invented, so the UI cannot drift into showing a 0. */
export const NOT_RECORDED = "not recorded";

export interface Provenance {
  id: string;
  project: { id: string; name: string };
  what: { measurement: string; discipline: string; value: number | null; unit: string };
  standing: {
    state: string;
    plain: string;
    reviewed_by: string;
    reviewed_at: string;
    may_be_priced: boolean;
  };
  machine_reading: {
    value: number | string;
    differs_from_value: boolean;
    note: string;
  };
  drawing: {
    name: string;
    id: string;
    filed_as: string;
    sheet: number | string;
    region: string;
  };
  method: string;
  analysis: { version: number | string; state: string; signed_off: string; confidence: string };
  used_in_estimates: string;
  limits: string[];
  notes: string;
}

export function getProvenance(quantityId: string) {
  return api<Provenance>(`/quantity-overrides/${quantityId}/provenance`);
}

export interface QuantityRow {
  id: string;
  discipline: string;
  quantity_type: string;
  value: number | null;
  unit: string;
  status: string;
  source: string;
}

export function listQuantities(projectId: string) {
  return api<QuantityRow[]>(
    `/quantity-overrides?project_id=${encodeURIComponent(projectId)}`,
  );
}
