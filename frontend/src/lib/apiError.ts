/**
 * The error for a failed API call, in words a person can act on.
 *
 * A response body is used only when it is our own JSON `detail`. Anything else
 * (nginx's HTML error page while the API restarts, a proxy's text) is never put
 * in the message: on 2026-10-14 the Scope page showed
 * "API 502: <html> <head><title>502 Bad Gateway…" to the user.
 */
export async function apiError(res: Response, path?: string): Promise<Error> {
  const text = await res.text().catch(() => "");
  let detail: string | null = null;
  try {
    const d = JSON.parse(text)?.detail;
    detail = typeof d === "string" ? d : d ? JSON.stringify(d) : null;
  } catch {
    /* not JSON: the status decides */
  }
  if (detail && detail.length < 400 && !detail.trimStart().startsWith("<")) {
    return new Error(`API ${res.status}: ${detail}`);
  }
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    return new Error(`API ${res.status}: The server is restarting or can't be reached. Wait a few seconds and try again.`);
  }
  return new Error(`API ${res.status}${path ? ` on ${path}` : ""}`);
}
