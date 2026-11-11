import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

/**
 * Same-origin proxy for the presentation proposal-PDF stream. Mirrors
 * `/api/workbench/estimate-client-pdf/...` — forwards the NextAuth session
 * cookie to the backend and streams the PDF back.
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
  { params }: { params: Promise<{ pid: string; prid: string }> },
) {
  const { pid, prid } = await params;
  const c = await cookies();
  const parts: string[] = [];
  for (const name of SESSION_COOKIES) {
    const v = c.get(name)?.value;
    if (v) parts.push(`${name}=${v}`);
  }
  const cookieHeader = parts.join("; ");

  let upstream: Response;
  try {
    upstream = await fetch(
      `${BACKEND}/projects/${pid}/workbench/presentations/${prid}/pdf`,
      { headers: { Cookie: cookieHeader }, cache: "no-store" },
    );
  } catch (e) {
    return new NextResponse(`Upstream fetch failed: ${String(e)}`, { status: 502 });
  }
  if (!upstream.ok) {
    return new NextResponse(`Upstream ${upstream.status}: ${await upstream.text()}`, {
      status: upstream.status,
    });
  }
  const dispo = upstream.headers.get("Content-Disposition") ?? 'inline; filename="proposal.pdf"';
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": dispo,
      "Cache-Control": "no-store",
    },
  });
}
