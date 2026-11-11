"use client";

// App Router error boundary scoped to the schedule page. Catches anything
// that throws during render / hydration — server-side fetches that bubbled
// past our try/catch, RSC serialization failures, client-component crashes.
// Without this, an unhandled throw bottoms out at Vercel's "This page
// couldn't load" overlay and we get zero signal.

import { useEffect } from "react";

export default function ScheduleError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surfaces in the browser console + Vercel function logs.
    console.error("[schedule/error.tsx]", error);
  }, [error]);

  return (
    <div className="rounded border border-red-300 bg-red-50 p-4 text-sm text-red-800 space-y-2">
      <div className="font-semibold">Schedule page failed to render</div>
      <pre className="whitespace-pre-wrap break-words text-xs">
        {error.message || String(error)}
      </pre>
      {error.digest && (
        <p className="text-xs text-red-700">
          Server digest: <code>{error.digest}</code>
        </p>
      )}
      <button
        type="button"
        onClick={reset}
        className="mt-1 px-3 py-1.5 rounded bg-red-700 text-white text-xs font-medium"
      >
        Try again
      </button>
    </div>
  );
}
