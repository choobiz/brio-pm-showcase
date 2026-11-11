import { auth } from "@/auth";
import { NextResponse } from "next/server";

export default auth((req) => {
  const isAuthed = !!req.auth;
  const path = req.nextUrl.pathname;
  const isAuthRoute = path.startsWith("/sign-in") || path.startsWith("/auth");
  // `/invoice/<token>` is the no-auth client invoice view; its API calls go
  // through `/api/backend/public/*`. Both must bypass the session gate.
  // PWA assets must load before/without a session (the service worker has to
  // register and the manifest/icon must be fetchable for install). The /field
  // pages themselves stay gated.
  const isPwaAsset =
    path === "/sw.js" ||
    path === "/manifest.webmanifest" ||
    path.startsWith("/icon-") ||
    path === "/apple-touch-icon.png" ||
    path === "/favicon.png" ||
    path === "/brio-logo.png";
  const isPublic =
    path === "/" ||
    isAuthRoute ||
    isPwaAsset ||
    path.startsWith("/api/auth") ||
    path.startsWith("/invoice/") ||
    path.startsWith("/api/backend/public/");

  if (!isAuthed && !isPublic) {
    return NextResponse.redirect(new URL("/sign-in", req.url));
  }
  return NextResponse.next();
});

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
