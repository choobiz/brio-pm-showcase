import Link from "next/link";
import { redirect } from "next/navigation";

import { auth } from "@/auth";

export default async function Home() {
  const session = await auth();
  if (session) {
    const role = (session.user as { role?: string } | undefined)?.role ?? "worker";
    if (role === "owner") redirect("/portal");
    if (role === "worker") redirect("/field");
    redirect("/dashboard");
  }

  return (
    <main className="min-h-screen flex items-center justify-center p-8">
      <div className="max-w-md w-full space-y-6 text-center">
        <h1 className="text-3xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>
          Keystone by BRIO
        </h1>
        <p className="text-slate-600">Your BRIO project — for owners, managers, and field crews.</p>
        <Link
          href="/sign-in"
          className="inline-block px-6 py-3 rounded-md text-white font-medium"
          style={{ background: "var(--color-brio-primary)" }}
        >
          Sign in
        </Link>
      </div>
    </main>
  );
}
