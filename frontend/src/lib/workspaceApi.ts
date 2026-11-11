// One way to call the backend from the workspace, and one way to fail.
//
// ⚠️ WHY THIS EXISTS. Every workspace lib used to end its fetch with
//
//     if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
//
// which is fine while the body is our own JSON and actively harmful the moment
// it is not. When the API container restarted on 2 Oct the body was nginx's
// error PAGE, so the register rendered this at the top of the screen:
//
//     Error: API 502: <html> <head><title>502 Bad Gateway</title></head> …
//
// Markup, in a red box, where a sentence belonged — and no indication that the
// right response was simply to wait twenty seconds and reload. The status code
// was the only part of that string worth reading.
//
// So: the BODY IS NEVER THE HEADLINE. A body is used only when it is JSON that
// carries a `detail` our own API wrote. Anything else — HTML, an empty body, a
// proxy's prose — is kept as `technical` for whoever is debugging and never
// shown as the message.
//
// This module is workspace-only, deliberately. The same throw appears in 25
// live-app libs and fixing those means editing the app the team uses today,
// which is outside the agreed scope of the preview build. They converge when
// the workspace is accepted; until then this is the only copy that changes.

const BASE = "/api/backend";

/** A failed call, in two registers: one for the person, one for the engineer. */
export class ApiError extends Error {
  /** HTTP status, or 0 when the request never reached a server at all. */
  readonly status: number;
  /** What actually came back. May be HTML. Never rendered as the message. */
  readonly technical: string | null;
  /** Whether trying the exact same thing again could plausibly work. */
  readonly retryable: boolean;

  constructor(status: number, message: string, technical: string | null, retryable: boolean) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.technical = technical;
    this.retryable = retryable;
  }
}

/** True when the text is a message and not a transport artefact.
 *
 *  A 502 body opens with `<html>`; a FastAPI detail does not. Length is part of
 *  the test because a stack trace is technically prose and still not something
 *  to put in a red box on a dashboard. */
function usableAsMessage(text: string): boolean {
  const t = text.trim();
  if (!t || t.length > 300) return false;
  if (t.startsWith("<")) return false;
  if (/<\/?(html|head|body|title|center|hr|pre)\b/i.test(t)) return false;
  return true;
}

/** Pull our own `detail` out of a JSON error body, if that is what this is. */
function detailFrom(text: string): string | null {
  try {
    const j = JSON.parse(text) as unknown;
    if (typeof j === "string") return usableAsMessage(j) ? j : null;
    if (j && typeof j === "object") {
      const d = (j as { detail?: unknown }).detail;
      if (typeof d === "string") return usableAsMessage(d) ? d : null;
      if (d && typeof d === "object") {
        const m = (d as { message?: unknown }).message;
        if (typeof m === "string" && usableAsMessage(m)) return m;
      }
    }
  } catch {
    /* not JSON — the status decides */
  }
  return null;
}

/** What to tell someone looking at the screen, keyed on status alone.
 *
 *  Written as consequences rather than codes: "502" tells a project manager
 *  nothing, "the server is restarting, try again shortly" tells them to wait
 *  rather than to report a bug. */
function humanMessage(status: number): { text: string; retryable: boolean } {
  if (status === 0)
    return { text: "Couldn't reach the server — check your connection and try again.", retryable: true };
  if (status === 401)
    return { text: "Your session has expired. Sign in again to continue.", retryable: false };
  if (status === 403)
    return { text: "You don't have access to this.", retryable: false };
  if (status === 404)
    return { text: "That isn't here — it may have been removed.", retryable: false };
  if (status === 409)
    return { text: "This changed while you were looking at it. Reload and check again.", retryable: false };
  if (status === 429)
    return { text: "Too many requests at once. Wait a moment and try again.", retryable: true };
  if (status === 502 || status === 503)
    return { text: "The server is restarting. This usually clears in under a minute.", retryable: true };
  if (status === 504)
    return { text: "The server took too long to answer. Try again.", retryable: true };
  if (status >= 500)
    return { text: "Something went wrong on the server.", retryable: true };
  return { text: "That request couldn't be completed.", retryable: false };
}

async function toApiError(res: Response): Promise<ApiError> {
  let body = "";
  try {
    body = await res.text();
  } catch {
    /* a body we cannot even read is not a body we can report */
  }
  const { text, retryable } = humanMessage(res.status);
  // Our own detail wins over the generic sentence — it is more specific and we
  // wrote it. Anything else is demoted to `technical`.
  const detail = detailFrom(body);
  return new ApiError(res.status, detail ?? text, body || null, retryable);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { cache: "no-store", ...init });
  } catch (e) {
    // fetch rejects for DNS, offline, CORS and connection-refused alike. The
    // raw TypeError ("Failed to fetch") is not something to show anyone.
    const { text, retryable } = humanMessage(0);
    throw new ApiError(0, text, e instanceof Error ? e.message : String(e), retryable);
  }
  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function workspaceGet<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function workspacePost<T>(path: string, body?: unknown): Promise<T> {
  if (body === undefined) return request<T>(path, { method: "POST" });
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function workspacePatch<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function workspaceDelete(path: string): Promise<void> {
  return request<void>(path, { method: "DELETE" });
}

export function workspacePut<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Normalise anything thrown into something safe to render.
 *
 *  Pages catch `unknown`, and `String(e)` on a raw Error gives "Error: …",
 *  which is how the nginx markup reached the screen in the first place. */
/** One line for an error held in string state. Keeps a plain Error's own
 *  message (often a useful validation reason) but never shows an HTML error
 *  page, which is what a 502 body is. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  const text = (e instanceof Error ? e.message : String(e)).replace(/^Error: /, "");
  return /<(!doctype|html|body)/i.test(text) ? "Something went wrong. Try again." : text;
}

export function describeError(e: unknown): {
  message: string;
  technical: string | null;
  retryable: boolean;
} {
  if (e instanceof ApiError)
    return { message: e.message, technical: e.technical, retryable: e.retryable };
  if (e instanceof Error)
    return { message: "Something went wrong.", technical: e.message, retryable: true };
  return { message: "Something went wrong.", technical: String(e), retryable: true };
}
