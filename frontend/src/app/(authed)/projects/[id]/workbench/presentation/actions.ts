"use server";

import { revalidatePath } from "next/cache";

import { setPriorClientEstimate, sharePresentation, type PriorClientEstimate } from "@/lib/workbench";

/** Generate a public, tokenized client link to the presentation proposal PDF. */
export async function sharePresentationAction(
  projectId: string,
  presentationId: string,
): Promise<{ url: string }> {
  const res = await sharePresentation(projectId, presentationId);
  return { url: res.url };
}

/** Persist the prior estimate the client already saw (for the deck's "why higher"
 * section). Survives every presentation regeneration. */
export async function setPriorClientEstimateAction(
  projectId: string,
  value: PriorClientEstimate | null,
): Promise<void> {
  await setPriorClientEstimate(projectId, value);
  revalidatePath(`/projects/${projectId}/workbench/presentation`);
}
