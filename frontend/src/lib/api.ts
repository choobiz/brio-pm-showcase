import { cookies, headers } from "next/headers";
import { apiError } from "./apiError";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8004";

/**
 * NextAuth v5 emits one of these cookie names for the encrypted session JWT.
 * We forward whichever one is present so the FastAPI backend can decrypt it
 * with `NEXTAUTH_SECRET` (must match between FE and BE) and resolve the user.
 */
const SESSION_COOKIE_NAMES = [
  "authjs.session-token",
  "__Secure-authjs.session-token",
  // legacy v4 names — harmless to forward:
  "next-auth.session-token",
  "__Secure-next-auth.session-token",
];

async function buildAuthHeaders(): Promise<Record<string, string>> {
  const out: Record<string, string> = {};

  // Forward the session cookie. We don't send the entire cookie jar — only
  // the auth cookie — to keep this surface small and debuggable.
  try {
    const c = await cookies();
    const parts: string[] = [];
    for (const name of SESSION_COOKIE_NAMES) {
      const v = c.get(name)?.value;
      if (v) parts.push(`${name}=${v}`);
    }
    if (parts.length) out["Cookie"] = parts.join("; ");
  } catch {
    // Not in a request context (e.g., build-time RSC) — just skip.
  }

  // Some deployments terminate cookies at the edge; preserve the original
  // x-forwarded-* headers for debug visibility on the backend.
  try {
    const h = await headers();
    const fwd = h.get("x-forwarded-for");
    if (fwd) out["X-Forwarded-For"] = fwd;
  } catch {
    // ignore
  }

  return out;
}

/**
 * Server-side fetch wrapper. Attaches the NextAuth session cookie so the
 * FastAPI backend can decrypt and identify the caller.
 *
 * Do NOT use from a client component — cookies()/headers() throw there.
 */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const authHeaders = await buildAuthHeaders();
  const cookiePresent = "Cookie" in authHeaders;
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders,
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
  } catch (e) {
    // Transport-level failure (DNS / TLS / Vercel runtime quirk) — include
    // enough breadcrumbs to triage without code spelunking.
    throw new Error(
      `API transport error on ${path}: ${String(e)} ` +
        `(base=${BASE}, cookie_present=${cookiePresent})`,
    );
  }
  if (!res.ok) {
    const e = await apiError(res, path);
    e.message += ` (cookie_present=${cookiePresent})`;
    throw e;
  }
  return res.json() as Promise<T>;
}
