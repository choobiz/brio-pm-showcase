import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

// Same-origin proxy that streams the Daily Site Report PDF from the backend.
const BACKEND = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8004";

const SESSION_COOKIES = [
  "authjs.session-token",
  "__Secure-authjs.session-token",
  "next-auth.session-token",
  "__Secure-next-auth.session-token",
];

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ rid: string }> },
) {
  const { rid } = await params;
  const c = await cookies();
  const parts: string[] = [];
  for (const name of SESSION_COOKIES) {
    const v = c.get(name)?.value;
    if (v) parts.push(`${name}=${v}`);
  }
  const cookieHeader = parts.join("; ");

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND}/daily-reports/${rid}/pdf`, {
      headers: { Cookie: cookieHeader },
      cache: "no-store",
    });
  } catch (e) {
    return new NextResponse(`Upstream fetch failed: ${String(e)}`, { status: 502 });
  }
  if (!upstream.ok) {
    return new NextResponse(`Upstream ${upstream.status}: ${await upstream.text()}`, {
      status: upstream.status,
    });
  }
  const dispo =
    upstream.headers.get("Content-Disposition") ??
    'inline; filename="daily-site-report.pdf"';
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": dispo,
      "Cache-Control": "no-store",
    },
  });
}
