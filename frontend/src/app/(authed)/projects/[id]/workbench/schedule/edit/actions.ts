"use server";

import { revalidatePath } from "next/cache";
import { createScheduleVersion } from "@/lib/workbench";

/**
 * Phase 4 Step 2 — server action: persist an edited schedule as a new
 * immutable schedule_version. Wraps the workbench lib call so the
 * client component (ScheduleEditor) doesn't need to import api.ts
 * (which uses next/headers and is server-only).
 */
export async function saveScheduleAction(
  projectId: string,
  body: Record<string, unknown>,
): Promise<{ id: string; version_number: number; critical_path_activity_codes: string[] }> {
  const res = await createScheduleVersion(projectId, body);
  revalidatePath(`/projects/${projectId}/workbench`, "layout");
  return res;
}
