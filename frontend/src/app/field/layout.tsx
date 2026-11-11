import Link from "next/link";

import { SignOutButton } from "@/components/SignOutButton";

export default function FieldLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header
        className="sticky top-0 z-10 flex items-center justify-between bg-[#0068A0] px-4 py-3 text-white"
        style={{ paddingTop: "max(0.75rem, env(safe-area-inset-top))" }}
      >
        <Link href="/field" className="flex items-center gap-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brio-logo.png" alt="BRIO" className="h-7 w-7 rounded bg-white" />
          <span className="text-base font-semibold tracking-tight">Keystone</span>
        </Link>
        <div className="flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-wide opacity-80">On-site</span>
          <SignOutButton variant="dark" />
        </div>
      </header>
      <main className="mx-auto max-w-md p-3 pb-28">{children}</main>
    </div>
  );
}
