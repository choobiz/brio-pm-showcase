"use client";

import { useState } from "react";
import { Card } from "@/components/ui/card";
import { describeError } from "@/lib/workspaceApi";

/**
 * How the workspace reports a failed call.
 *
 * Three things it must do, learned from the 502 that rendered nginx's HTML in a
 * red box on the register:
 *
 *   1. Say what happened in a sentence, not a status line.
 *   2. Offer the action that actually resolves it. A restarting server needs a
 *      retry, not a bug report, and the screen should say so rather than
 *      leaving a project manager to guess whether their data is gone.
 *   3. Keep the technical detail reachable, because somebody eventually has to
 *      debug it — but behind a disclosure, not as the headline.
 *
 * Non-retryable states get no retry button. An expired session is not fixed by
 * pressing the same button again, and offering it would be a lie about what is
 * wrong.
 */
export function WorkspaceError({
  error,
  onRetry,
  className = "",
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const { message, technical, retryable } = describeError(error);
  const [showDetail, setShowDetail] = useState(false);

  return (
    <Card className={`border-red-200 bg-red-50 p-4 ${className}`}>
      <p className="text-sm font-medium text-red-800">{message}</p>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        {retryable && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="rounded border border-red-300 bg-white px-3 py-1 text-xs font-medium
                       text-red-800 hover:bg-red-100"
          >
            Try again
          </button>
        )}
        {technical && (
          <button
            type="button"
            onClick={() => setShowDetail((v) => !v)}
            className="text-xs text-red-700 underline underline-offset-2 hover:text-red-900"
          >
            {showDetail ? "Hide details" : "Details"}
          </button>
        )}
      </div>

      {showDetail && technical && (
        // Pre-wrapped and scroll-capped: the body that caused this was a whole
        // HTML document, and an unbounded dump would push the page off screen
        // exactly when someone is trying to read it.
        <pre
          className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-red-200
                     bg-white p-2 text-[11px] leading-snug text-red-900"
        >
          {technical}
        </pre>
      )}
    </Card>
  );
}
