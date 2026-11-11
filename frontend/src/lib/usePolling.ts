"use client";

import { useEffect, useRef } from "react";

/**
 * Poll `fn` every `intervalMs`, but ONLY while the tab is visible.
 *
 * Every poller in this app reaches the backend through `/api/backend/[...path]`,
 * which is `force-dynamic` on the Node runtime — so one tick is one billable
 * serverless invocation, whether or not anybody is looking at the result. A
 * background tab polling at full rate costs exactly what a watched one costs
 * and tells nobody anything, which is how forgotten tabs quietly spent a
 * month's platform CPU budget.
 *
 * Two deliberate properties:
 *
 *   * Becoming visible fires `fn` IMMEDIATELY, before the interval resumes.
 *     Pausing while hidden is only safe if coming back is instant; otherwise
 *     the user stares at stale data for up to a full period and learns not to
 *     trust the screen.
 *   * `fn` is held in a ref, so a caller that redefines it every render — the
 *     normal case here, since these callbacks close over the state they also
 *     set — does not restart the timer. Restarting on every tick means the
 *     interval never actually elapses at the stated period, which is what the
 *     documents-page badge poller was doing.
 */
export function useVisiblePolling(
  fn: () => void | Promise<unknown>,
  intervalMs: number,
  enabled: boolean,
) {
  const saved = useRef(fn);
  saved.current = fn;

  useEffect(() => {
    if (!enabled) return;

    let timer: number | undefined;
    const tick = () => void saved.current();

    const start = () => {
      if (timer === undefined) timer = window.setInterval(tick, intervalMs);
    };
    const halt = () => {
      if (timer !== undefined) {
        window.clearInterval(timer);
        timer = undefined;
      }
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        tick();
        start();
      } else {
        halt();
      }
    };

    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      halt();
    };
  }, [enabled, intervalMs]);
}
