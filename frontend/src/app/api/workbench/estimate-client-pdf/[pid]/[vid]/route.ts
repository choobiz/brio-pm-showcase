import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

/**
 * Same-origin proxy for the client-edition (Investment Proposal) PDF
 * stream. Mirrors `/api/workbench/estimate-pdf/[pid]/[vid]/route.ts` but
 * hits the client-pdf endpoint on the backend.
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
  const fwd = new URLSearchParams();
  if (req.nextUrl.searchParams.get("summary") === "true") fwd.set("summary", "true");
  if (req.nextUrl.searchParams.get("residential") === "true") fwd.set("residential", "true");
  if (req.nextUrl.searchParams.get("internal_fee") === "true") fwd.set("internal_fee", "true");
  const qs = fwd.toString() ? `?${fwd.toString()}` : "";
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
      `${BACKEND}/projects/${pid}/workbench/estimate-versions/${vid}/client-pdf${qs}`,
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

  const dispo =
    upstream.headers.get("Content-Disposition") ??
    'inline; filename="project-estimate.pdf"';
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": dispo,
      "Cache-Control": "no-store",
    },
  });
}
