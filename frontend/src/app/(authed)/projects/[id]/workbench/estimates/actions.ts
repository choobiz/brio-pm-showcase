"use server";

import { revalidatePath } from "next/cache";
import {
  activateEstimateVersion,
  activateWithRegeneration,
  createEstimateVersion,
  getRegenerationPlan,
  updateEstimateVersion,
  type RegenerationPlan,
  type RegenerationResult,
} from "@/lib/workbench";

/**
 * Phase C — preview what activating this estimate will regenerate downstream,
 * with a per-artifact token-cost breakdown (drives the approval modal).
 */
export async function getRegenerationPlanAction(
  projectId: string,
  versionId: string,
): Promise<RegenerationPlan> {
  return getRegenerationPlan(projectId, versionId);
}

/**
 * Phase C — management-approved: activate this estimate AND regenerate the
 * downstream documents (scope/schedule/presentation via AI; cash flow/SOV/
 * contract deterministically). Owner-gated on the backend.
 */
export async function activateWithRegenerationAction(
  projectId: string,
  versionId: string,
): Promise<RegenerationResult> {
  const res = await activateWithRegeneration(projectId, versionId);
  revalidatePath(`/projects/${projectId}/workbench`, "layout");
  return res;
}

/**
 * Server action: mark an estimate version as the project's active anchor.
 *
 * After the activation, every workbench tab needs to re-read because the
 * is_stale flag on downstream artifacts (SOV / cash flow / contract) is
 * computed against the active estimate. revalidatePath() on /workbench
 * invalidates the whole workbench sub-tree.
 */
export async function activateEstimateAction(
  projectId: string,
  versionId: string,
): Promise<void> {
  await activateEstimateVersion(projectId, versionId);
  revalidatePath(`/projects/${projectId}/workbench`, "layout");
}

/**
 * Phase 3b — server action: persist edits as a new immutable estimate version.
 *
 * Caller (in-browser EstimateEditor) has already done the rollup arithmetic
 * and passes a fully-recomputed payload. We don't reconcile here — the
 * client owns the math, the server stores it and marks active.
 *
 * Returns the new version ID so the page can redirect into its detail view.
 */
export async function saveEditedEstimateAction(
  projectId: string,
  parentVersionId: string,
  payload: Record<string, unknown>,
  meta: {
    notes?: string;
    overhead_pct?: number;
    profit_pct?: number;
    reference?: string;
    rate_updates?: { key: string; write: boolean; reason?: string }[];
  },
): Promise<{ id: string; version_number: number }> {
  const res = await createEstimateVersion(projectId, {
    parent_version_id: parentVersionId,
    payload,
    notes: meta.notes,
    overhead_pct: meta.overhead_pct,
    profit_pct: meta.profit_pct,
    reference: meta.reference,
    rate_updates: meta.rate_updates,
  });
  revalidatePath(`/projects/${projectId}/workbench`, "layout");
  return { id: res.id, version_number: res.version_number };
}

/**
 * Phase 3b — server action: update an existing estimate version IN PLACE.
 *
 * Same payload shape as saveEditedEstimateAction, but mutates the row
 * instead of inserting a new one. Use when iterating on numbers and you
 * don't want every save to bump the version chain.
 */
export async function updateExistingEstimateAction(
  projectId: string,
  versionId: string,
  payload: Record<string, unknown>,
  meta: {
    notes?: string;
    overhead_pct?: number;
    profit_pct?: number;
    reference?: string;
    rate_updates?: { key: string; write: boolean; reason?: string }[];
  },
): Promise<{ id: string; version_number: number }> {
  const res = await updateEstimateVersion(projectId, versionId, {
    payload,
    notes: meta.notes,
    overhead_pct: meta.overhead_pct,
    profit_pct: meta.profit_pct,
    reference: meta.reference,
    rate_updates: meta.rate_updates,
  });
  revalidatePath(`/projects/${projectId}/workbench`, "layout");
  return { id: res.id, version_number: res.version_number };
}
