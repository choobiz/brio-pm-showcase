import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

/**
 * Phase 5 — same-origin proxy for the backend PDF stream.
 *
 * The estimate-version PDF endpoint requires the NextAuth session cookie.
 * If we link the user's browser straight at the FastAPI host, the cookie
 * doesn't ride along (different origin). Routing through this Next.js
 * handler keeps the request same-origin, lets us forward the cookie on
 * the server side, and streams the PDF back unchanged.
 */
const BACKEND = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8004";

const SESSION_COOKIES = [
  "authjs.session-token",
  "__Secure-authjs.session-token",
  "next-auth.session-token",
  "__Secure-next-auth.session-token",
];

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ pid: string; vid: string }> },
) {
  const { pid, vid } = await params;
  const c = await cookies();
  const parts: string[] = [];
  for (const name of SESSION_COOKIES) {
    const v = c.get(name)?.value;
    if (v) parts.push(`${name}=${v}`);
  }
  const cookieHeader = parts.join("; ");

  // Wrap the upstream call so transport-level errors (DNS / TLS / timeout
  // on the Vercel runtime) come back as a readable 502 instead of a crashed
  // tab — and surface upstream non-2xx responses verbatim.
  let upstream: Response;
  try {
    upstream = await fetch(
      `${BACKEND}/projects/${pid}/workbench/estimate-versions/${vid}/pdf`,
      { headers: { Cookie: cookieHeader }, cache: "no-store" },
    );
  } catch (e) {
    return new NextResponse(
      `Upstream fetch failed: ${String(e)} ` +
        `(backend=${BACKEND}, cookie_present=${cookieHeader.length > 0})`,
      { status: 502 },
    );
  }

  if (!upstream.ok) {
    return new NextResponse(
      `Upstream ${upstream.status}: ${await upstream.text()}\n` +
        `(backend=${BACKEND}, cookie_present=${cookieHeader.length > 0})`,
      { status: upstream.status },
    );
  }

  const dispo = upstream.headers.get("Content-Disposition") ?? 'inline; filename="estimate.pdf"';
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": dispo,
      "Cache-Control": "no-store",
    },
  });
}
