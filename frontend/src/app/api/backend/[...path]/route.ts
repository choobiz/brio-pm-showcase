import { cookies } from "next/headers";
import type { NextRequest } from "next/server";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8004";

const SESSION_COOKIE_NAMES = [
  "authjs.session-token",
  "__Secure-authjs.session-token",
  "next-auth.session-token",
  "__Secure-next-auth.session-token",
];

async function buildCookieHeader(): Promise<string | null> {
  try {
    const c = await cookies();
    const parts: string[] = [];
    for (const name of SESSION_COOKIE_NAMES) {
      const v = c.get(name)?.value;
      if (v) parts.push(`${name}=${v}`);
    }
    return parts.length ? parts.join("; ") : null;
  } catch {
    return null;
  }
}

async function forward(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await ctx.params;
  const query = req.nextUrl.search ?? "";
  const url = `${BASE}/${path.join("/")}${query}`;

  const cookieHeader = await buildCookieHeader();
  const headers: Record<string, string> = {};
  const ct = req.headers.get("content-type");
  if (ct) headers["Content-Type"] = ct;
  const accept = req.headers.get("accept");
  if (accept) headers["Accept"] = accept;
  if (cookieHeader) headers["Cookie"] = cookieHeader;
  // Who and from where, for records like a client accepting a proposal.
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) headers["X-Forwarded-For"] = fwd;
  const ua = req.headers.get("user-agent");
  if (ua) headers["User-Agent"] = ua;

  const init: RequestInit = {
    method: req.method,
    headers,
    cache: "no-store",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }

  const upstream = await fetch(url, init);

  const resHeaders = new Headers();
  for (const [k, v] of upstream.headers.entries()) {
    const lk = k.toLowerCase();
    if (lk === "content-encoding" || lk === "transfer-encoding" || lk === "content-length") continue;
    resHeaders.set(k, v);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: resHeaders,
  });
}

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const PUT = forward;
export const DELETE = forward;
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
