// Schedule actuals — writes from the browser, via the /api/backend proxy.
// (Reads happen server-side through lib/workbench getActuals.)

import { apiError } from "@/lib/apiError";
import type { ActivityActual } from "@/lib/workbench";

export async function recordActual(pid: string, code: string, body: Record<string, unknown>): Promise<ActivityActual> {
  const path = `/projects/${pid}/schedule/actuals/${encodeURIComponent(code)}`;
  const r = await fetch(`/api/backend${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!r.ok) throw await apiError(r, path);
  return r.json();
}

export type ReplanSaved = {
  id: string;
  version_number: number;
  p50_finish_date: string;
  p80_finish_date: string;
  previous_p50_finish_date: string;
  client_visible: boolean;
};

/** Save the re-plan as the next schedule version. 409 when a newer version
 *  was saved since the preview. */
export async function saveReplan(
  pid: string,
  body: { base_schedule_id: string; unanswered: "keep" | "push"; client_visible: boolean; note?: string },
): Promise<ReplanSaved> {
  const path = `/projects/${pid}/schedule/replan`;
  const r = await fetch(`/api/backend${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!r.ok) throw await apiError(r, path);
  return r.json();
}
