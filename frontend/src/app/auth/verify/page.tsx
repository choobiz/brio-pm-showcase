export default function VerifyPage() {
  return (
    <main className="min-h-screen flex items-center justify-center p-8">
      <div className="max-w-md w-full text-center bg-white rounded-lg shadow p-8 space-y-3">
        <h1 className="text-2xl font-semibold" style={{ color: "var(--color-brio-accent)" }}>
          Check your email
        </h1>
        <p className="text-slate-600">
          We sent you a magic link. Click it to sign in.
        </p>
      </div>
    </main>
  );
}
