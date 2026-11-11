import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

import { selectionPdfMode } from "@/lib/selectionPdfModes";

/**
 * Same-origin proxy for the Labor + Material-Selection estimate PDF (the 5th
 * estimate type). Mirrors estimate-client-pdf; hits the selection-pdf endpoint.
 * ?mode=selection (default) | detailed | lumpsum | scopepack (&scope_id=…:
 * the project's scope, then selections with no prices, then the lump sum) |
 * divisions (priced by CSI division, Div 09 by scope). See lib/selectionPdfModes.
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
  const rawMode = req.nextUrl.searchParams.get("mode");
  const mode = selectionPdfMode(rawMode);
  const scopeId = req.nextUrl.searchParams.get("scope_id");
  const scopeQs = scopeId && /^[0-9a-f-]{36}$/i.test(scopeId) ? `&scope_id=${scopeId}` : "";
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
      `${BACKEND}/projects/${pid}/workbench/estimate-versions/${vid}/selection-pdf?mode=${mode}${scopeQs}`,
      { headers: { Cookie: cookieHeader }, cache: "no-store" },
    );
  } catch (e) {
    return new NextResponse(
      `Upstream fetch failed: ${String(e)} (backend=${BACKEND}, cookie_present=${cookieHeader.length > 0})`,
      { status: 502 },
    );
  }

  if (!upstream.ok) {
    return new NextResponse(
      `Upstream ${upstream.status}: ${await upstream.text()}\n(backend=${BACKEND}, cookie_present=${cookieHeader.length > 0})`,
      { status: upstream.status },
    );
  }

  const dispo =
    upstream.headers.get("Content-Disposition") ??
    'inline; filename="estimate-material-selection.pdf"';
  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": dispo,
      "Cache-Control": "no-store",
    },
  });
}
