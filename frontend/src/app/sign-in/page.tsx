import { signIn } from "@/auth";

type SearchParams = Promise<{ error?: string; email?: string }>;

const ERROR_MESSAGES: Record<string, string> = {
  AccessDenied: "This email is not invited yet. Contact Amir to request access.",
  Configuration: "Sign-in is not configured. Check the server logs.",
  Verification: "That magic link has expired or already been used. Request a new one.",
  Default: "Something went wrong. Try again.",
};

export default async function SignInPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const { error, email } = await searchParams;
  const errorMessage = error
    ? ERROR_MESSAGES[error] ?? ERROR_MESSAGES.Default
    : null;

  return (
    <main className="min-h-screen flex items-center justify-center p-8">
      <div className="max-w-md w-full space-y-6 bg-white rounded-lg shadow p-8">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/brio-logo.png" alt="BRIO" className="h-14 w-14" />
        <h1
          className="text-2xl font-semibold"
          style={{ color: "var(--color-brio-accent)" }}
        >
          Sign in to Keystone
        </h1>
        <p className="text-slate-600 text-sm">
          We will email you a magic link. No passwords.
        </p>
        {errorMessage ? (
          <div
            role="alert"
            className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"
          >
            {errorMessage}
          </div>
        ) : null}
        <form
          action={async (formData) => {
            "use server";
            await signIn("nodemailer", {
              email: formData.get("email"),
              redirectTo: "/projects",
            });
          }}
          className="space-y-4"
        >
          <input
            type="email"
            name="email"
            required
            defaultValue={email ?? ""}
            placeholder="you@brioconstruction.ca"
            className="w-full border border-slate-300 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brio-primary"
          />
          <button
            type="submit"
            className="w-full text-white font-medium py-2.5 rounded-md"
            style={{ background: "var(--color-brio-primary)" }}
          >
            Send magic link
          </button>
        </form>
      </div>
    </main>
  );
}
