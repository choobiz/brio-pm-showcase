"use client";

import { signOut } from "next-auth/react";

/**
 * Sign out, from any surface. A client component so it can drop into both the
 * server-rendered office/field layouts and the client-rendered owner portal —
 * one implementation instead of a server action per layout.
 *
 * `variant` matches the chrome it sits in: the office sidebar is light, the
 * field + portal headers are dark brand bars.
 */
export function SignOutButton({ variant = "light" }: { variant?: "light" | "dark" }) {
  const cls =
    variant === "dark"
      ? "rounded-full px-3 py-1.5 text-xs font-medium text-white/90 hover:text-white"
      : "w-full rounded-md px-3 py-2 text-left text-sm text-slate-600 hover:bg-slate-100 hover:text-slate-900";

  return (
    <button
      type="button"
      onClick={() => signOut({ redirectTo: "/sign-in" })}
      className={cls}
      style={variant === "dark" ? { background: "rgba(255,255,255,0.12)" } : undefined}
    >
      Sign out
    </button>
  );
}
