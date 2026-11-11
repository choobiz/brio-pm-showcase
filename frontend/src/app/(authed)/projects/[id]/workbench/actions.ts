"use server";

import { revalidatePath } from "next/cache";
import { regenerateDownstream } from "@/lib/workbench";

/**
 * Phase 3a Step 5 — server action: regenerate downstream artifacts
 * (Contract / SOV / Cash Flow) against the project's active estimate.
 *
 * After this completes the StaleBanner instances refresh — Contract +
 * SOVs flip to ✓ Up to date (their based_on_estimate_id = active.id).
 * Cash Flow also flips green but its weekly numbers are unchanged
 * (we only re-pointed it); the appended note tells the user to rerun
 * the billing plan (Contract) for accurate numbers.
 */
export async function regenerateDownstreamAction(projectId: string) {
  const summary = await regenerateDownstream(projectId);
  revalidatePath(`/projects/${projectId}/workbench`, "layout");
  return summary;
}
