import Link from "next/link";
import { redirect } from "next/navigation";

import { auth } from "@/auth";
import { AppSidebar } from "@/components/AppSidebar";
import { AskPanel } from "@/components/AskPanel";
import { SignOutButton } from "@/components/SignOutButton";

export default async function AuthedLayout({ children }: { children: React.ReactNode }) {
  const session = await auth();
  const role = (session?.user as { role?: string } | undefined)?.role ?? "worker";
  // The office app is for managers + superadmins. Field crew and clients are
  // sent to their own surfaces (owner portal lands here later).
  if (session && role !== "manager" && role !== "superadmin") {
    redirect(role === "owner" ? "/portal" : "/field");
  }
  const isSuper = role === "superadmin";

  return (
    <div className="min-h-screen flex">
      <AppSidebar
        items={[
          // Named as in the round-1 wireframe (Home, Projects, Clients, Sales…).
          // The pages behind them are unchanged until each is rebuilt.
          { href: "/dashboard", label: "Home", icon: "dashboard" },
          { href: "/projects", label: "Projects", icon: "projects" },
          // Clients, Sales, Skills and Reviews are superadmin-only (Amir, 2026-10-15).
          ...(isSuper
            ? [
                { href: "/clients", label: "Clients", icon: "clients" },
                { href: "/crm", label: "Sales", icon: "crm" },
              ]
            : []),
          // Round 2 (R2Libraries / R2Settings): two groups instead of eight items.
          {
            href: "/libraries", label: "Libraries", icon: "rates",
            children: [
              { href: "/libraries", label: "Overview" },
              { href: "/rates", label: "Rates & catalogue" },
              { href: "/trades", label: "Trades" },
              { href: "/divisions", label: "Cost divisions" },
              { href: "/analytics", label: "Cost Intelligence" },
            ],
          },
          {
            href: "/settings", label: "Settings", icon: "users",
            children: [
              { href: "/settings", label: "Overview" },
              ...(isSuper ? [{ href: "/admin/users", label: "Users & roles" }] : []),
              { href: "/ai-setup", label: "AI setup" },
              ...(isSuper
                ? [
                    { href: "/skills", label: "Writing rules (Skills)" },
                    { href: "/reviews", label: "Review requests" },
                  ]
                : []),
            ],
          },
        ]}
        footer={
          <>
            {/* Who you are + how to leave. The identity line is deliberate:
                access is per-account (superadmin sees every project, everyone
                else needs a ProjectMember row), so "which account am I?" is the
                first thing to check when a project 403s. */}
            {session?.user?.email && (
              <div className="px-3 pb-1.5">
                <div
                  className="truncate text-xs text-slate-600"
                  title={session.user.email}
                >
                  {session.user.email}
                </div>
                <div className="text-[11px] uppercase tracking-wide text-slate-400">
                  {role}
                </div>
              </div>
            )}
            <SignOutButton />
          </>
        }
      />
      <main className="min-w-0 flex-1 p-3 sm:p-6">{children}</main>
      <AskPanel />
    </div>
  );
}
