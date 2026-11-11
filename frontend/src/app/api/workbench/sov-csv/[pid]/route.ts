import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

/** Same-origin proxy for the Schedule of Values CSV export. ?scenario=base|combined */
const BACKEND = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8004";

const SESSION_COOKIES = [
  "authjs.session-token",
  "__Secure-authjs.session-token",
  "next-auth.session-token",
  "__Secure-next-auth.session-token",
];

export async function GET(req: NextRequest, { params }: { params: Promise<{ pid: string }> }) {
  const { pid } = await params;
  const scenario = req.nextUrl.searchParams.get("scenario") ?? "base";
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
      `${BACKEND}/projects/${pid}/workbench/sov-csv?scenario=${encodeURIComponent(scenario)}`,
      { headers: { Cookie: cookieHeader }, cache: "no-store" },
    );
  } catch (e) {
    return new NextResponse(`Upstream fetch failed: ${String(e)}`, { status: 502 });
  }
  if (!upstream.ok) {
    return new NextResponse(`Upstream ${upstream.status}: ${await upstream.text()}`, { status: upstream.status });
  }
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/csv",
      "Content-Disposition": upstream.headers.get("Content-Disposition") ?? 'attachment; filename="SOV.csv"',
      "Cache-Control": "no-store",
    },
  });
}
