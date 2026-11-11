"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { SignOutButton } from "@/components/SignOutButton";

import { PortalProvider } from "./portalCtx";

const NAV = [
  { href: "/portal", label: "Home", icon: "⌂" },
  { href: "/portal/progress", label: "Progress", icon: "◈" },
  { href: "/portal/photos", label: "Photos", icon: "❏" },
  { href: "/portal/selections", label: "Selections", icon: "☑" },
  { href: "/portal/proposal", label: "Proposal", icon: "✎" },
  { href: "/portal/payments", label: "Payments", icon: "◫" },
  { href: "/portal/documents", label: "Documents", icon: "▤" },
  { href: "/portal/warranty", label: "Warranty", icon: "⛨" },
];

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <PortalProvider>
      <div className="min-h-screen" style={{ background: "#FAF9F7", color: "#26303a" }}>
        {/* Top brand bar */}
        <header
          className="sticky top-0 z-20 flex items-center justify-between px-5 py-3.5 border-b"
          style={{ background: "#1D3146", borderColor: "rgba(255,255,255,0.08)" }}
        >
          <div className="flex items-center gap-2.5">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/brio-logo.png" alt="BRIO" className="h-7 w-7" />
            <span className="text-white font-semibold tracking-wide text-sm">Keystone</span>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/portal/messages"
              className="text-xs font-medium px-3 py-1.5 rounded-full"
              style={{ background: "rgba(255,255,255,0.12)", color: "#fff" }}
            >
              ✉ Message us
            </Link>
            <SignOutButton variant="dark" />
          </div>
        </header>

        {/* Content — comfortable reading width, generous padding */}
        <main className="mx-auto w-full max-w-2xl px-5 pt-6 pb-28">{children}</main>

        {/* Bottom tab nav (mobile-first; centered on desktop) */}
        <nav
          className="fixed bottom-0 inset-x-0 z-20 border-t bg-white/95 backdrop-blur"
          style={{ borderColor: "#ece8e2" }}
        >
          <div className="mx-auto max-w-2xl grid grid-cols-6">
            {NAV.map((n) => {
              const active = pathname === n.href;
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  className="flex flex-col items-center gap-0.5 py-2.5 text-[11px] font-medium transition"
                  style={{ color: active ? "#0068A0" : "#93a0ad" }}
                >
                  <span className="text-lg leading-none">{n.icon}</span>
                  {n.label}
                </Link>
              );
            })}
          </div>
        </nav>
      </div>
    </PortalProvider>
  );
}
