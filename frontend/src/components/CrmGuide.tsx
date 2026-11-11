"use client";

/**
 * The CRM guide.
 *
 * Deliberately explains THIS data rather than "how CRMs work" — where the
 * firms came from, what the tiers actually mean, and which numbers can be
 * trusted. A generic tour would be worse than none, because the non-obvious
 * parts here are all specific: that a blank registry field is not a dead
 * company, that medium-confidence emails were matched by an algorithm that
 * sometimes binds the wrong firm, and that an opportunity with no next action
 * is the most urgent thing on the screen.
 */

import { useCallback, useEffect, useState } from "react";

const SEEN_KEY = "brio-crm-guide-seen-v1";

interface Step {
  title: string;
  body: React.ReactNode;
}

const STEPS: Step[] = [
  {
    title: "This is a queue, not a contact list",
    body: (
      <>
        <p>
          The screen opens on what needs attention today — usually a handful of
          firms, not the four thousand behind them. If it ever shows you a wall
          of contacts, something has gone wrong.
        </p>
        <p className="mt-2">
          Work top to bottom, log what happened, and it refills itself
          tomorrow.
        </p>
      </>
    ),
  },
  {
    title: "The top row is the one with no next action",
    body: (
      <>
        <p>
          Rows sort by urgency, and <strong>“no next action” sorts above even
          overdue</strong>. That is deliberate: an overdue item is being worked
          and simply slipped. An item with no next step is open, live, and going
          nowhere — and nothing will remind you about it except this.
        </p>
        <p className="mt-2 text-slate-600">
          Then: overdue (most overdue first), then due today.
        </p>
      </>
    ),
  },
  {
    title: "Where these firms came from",
    body: (
      <>
        <p>
          Every firm here was pulled from municipal building permits across
          eight sources — Vancouver, Surrey, New Westminster, City of North
          Vancouver, Burnaby, Richmond, Coquitlam and Delta.
        </p>
        <p className="mt-2">
          The useful discovery: <strong>most permits are filed by design firms,
          not owners</strong>, and firms that <em>file</em> permits mostly do not{" "}
          <em>build</em> them. Those firms are referral partners, not
          competitors — which is why they are the primary target.
        </p>
      </>
    ),
  },
  {
    title: "What the tiers mean",
    body: (
      <ul className="space-y-1.5">
        <li><strong>A</strong> — 20+ permits a year. Worth a direct call; one relationship is worth dozens of projects.</li>
        <li><strong>B</strong> — 6 to 19. Personalised email naming two or three of their actual projects.</li>
        <li><strong>C</strong> — 2 to 5. Templated, but reference their specific permit.</li>
        <li><strong>D</strong> — one permit. Low effort, qualify on reply.</li>
        <li className="pt-1 text-slate-600">
          Only A and B start in the queue. C and D sit in the list until you
          promote them — otherwise day one would bury you.
        </li>
      </ul>
    ),
  },
  {
    title: "Check anything marked “unverified”",
    body: (
      <>
        <p>
          Contact details were found automatically, and the matching is honest
          about its own limits.
        </p>
        <ul className="mt-2 space-y-1.5">
          <li>
            <span className="rounded bg-emerald-50 px-1 text-emerald-800">high</span>{" "}
            — the domain carries the firm’s own name. Safe to use.
          </li>
          <li>
            <span className="rounded bg-amber-50 px-1 text-amber-800">unverified</span>{" "}
            — matched on a weaker signal. <strong>Look before you send.</strong> One
            firm was bound to a video-game project that shared its initials.
          </li>
        </ul>
        <p className="mt-2 text-slate-600">
          Where nothing could be found confidently, the field is blank rather
          than guessed.
        </p>
      </>
    ),
  },
  {
    title: "A blank registry field is not a dead company",
    body: (
      <>
        <p>
          Firms were checked against the BC corporate registry. A registration
          number and <strong>ACT</strong> means they are trading.
        </p>
        <p className="mt-2">
          But <strong>“not registered” usually means a sole proprietor trading
          under their own name</strong> — who has no obligation to register in
          BC — or a permit that recorded an abbreviation. It is not a reason to
          skip them.
        </p>
        <p className="mt-2 text-slate-600">
          Genuinely dissolved companies were found separately and are excluded
          from the queue entirely.
        </p>
      </>
    ),
  },
  {
    title: "Always leave a next action",
    body: (
      <>
        <p>
          <strong>Log</strong> records the call and sets what happens next in the
          same form. That pairing is the point: a logged call with no next step
          is how a good conversation quietly dies.
        </p>
        <p className="mt-2">
          If it is genuinely finished, set the status to won, lost or dormant —
          then no next action is needed and it leaves the queue cleanly.
        </p>
      </>
    ),
  },
];

export function CrmGuide({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [i, setI] = useState(0);

  const close = useCallback(() => {
    try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* private mode */ }
    setI(0);
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key === "ArrowRight" && i < STEPS.length - 1) setI(i + 1);
      if (e.key === "ArrowLeft" && i > 0) setI(i - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, i, close]);

  if (!open) return null;
  const step = STEPS[i];
  const last = i === STEPS.length - 1;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="CRM guide"
      onClick={close}
    >
      <div
        className="w-full max-w-lg rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-slate-200 px-5 py-3">
          <p className="text-[11px] uppercase tracking-wide text-slate-400">
            Guide · {i + 1} of {STEPS.length}
          </p>
          <h2 className="mt-0.5 text-lg font-semibold tracking-tight">{step.title}</h2>
        </div>

        <div className="px-5 py-4 text-sm leading-relaxed">{step.body}</div>

        <div className="flex items-center justify-between gap-3 border-t border-slate-200 px-5 py-3">
          <div className="flex gap-1.5" aria-hidden="true">
            {STEPS.map((_, n) => (
              <button
                key={n}
                onClick={() => setI(n)}
                className={`h-1.5 w-1.5 rounded-full transition ${
                  n === i ? "bg-slate-800" : "bg-slate-300 hover:bg-slate-400"
                }`}
              />
            ))}
          </div>
          <div className="flex gap-2">
            <button onClick={close}
                    className="rounded px-3 py-1.5 text-sm text-slate-500 hover:text-slate-800">
              {last ? "Close" : "Skip"}
            </button>
            {i > 0 && (
              <button onClick={() => setI(i - 1)}
                      className="rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">
                Back
              </button>
            )}
            <button
              onClick={() => (last ? close() : setI(i + 1))}
              className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
            >
              {last ? "Start working" : "Next"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** Shows the guide automatically on a first visit, and never again unasked. */
export function useCrmGuide() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    try {
      if (!localStorage.getItem(SEEN_KEY)) setOpen(true);
    } catch { /* private mode — just don't auto-open */ }
  }, []);
  return { open, setOpen };
}
