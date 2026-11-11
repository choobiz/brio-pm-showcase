/**
 * A fetch whose failure must not look like "nothing there" (#59, 2026-10-05:
 * during a deploy, pages showed "No contract yet" / "No notes yet" on projects
 * that had them, because `.catch(() => [])` turned an outage into an empty
 * list). Returns the data, or `failed: true` with the detail sent to the
 * server log only — the reader gets <LoadFailed/>, never an error body.
 */
export async function loadOr<T>(what: string, p: Promise<T>): Promise<{ data: T | null; failed: boolean }> {
  try {
    return { data: await p, failed: false };
  } catch (e) {
    console.error(`[load failed] ${what}`, e);
    return { data: null, failed: true };
  }
}
