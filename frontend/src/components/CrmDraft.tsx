"use client";

/**
 * Per-tier opening message, built from the firm's own permit record.
 *
 * The point is not to save typing — it is that a message naming what this firm
 * actually does lands differently from one that doesn't, and composing that
 * 45 times by hand is where outreach quietly stops happening. Everything
 * interpolated below comes from their permits, so the specificity is real
 * rather than flattery.
 *
 * Tier A gets talking points rather than an email: at 20+ permits a year they
 * are worth a call, and a script that reads like a template on the phone is
 * worse than no script.
 */

import { useState } from "react";

import type { CrmContactDetail } from "@/lib/crm";

function money(n: number | null): string {
  if (!n) return "";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(0)}M`;
  return `$${Math.round(n / 1_000)}k`;
}

function cities(c: string | null): string {
  const list = (c ?? "").split("|").filter(Boolean);
  if (!list.length) return "Metro Vancouver";
  if (list.length === 1) return list[0];
  if (list.length === 2) return `${list[0]} and ${list[1]}`;
  return `${list[0]}, ${list[1]} and elsewhere`;
}

/** "2550 E 3rd Avenue" — the street, without the city and postcode tail. */
function street(address: string): string {
  return (address.split(",")[0] ?? address).trim();
}

/** "May 2025". A bare ISO date in a sentence reads like a database field. */
function when(issued: string | null): string {
  if (!issued) return "";
  const d = new Date(`${issued}T00:00:00`);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-CA", { month: "long", year: "numeric" });
}

type Project = NonNullable<CrmContactDetail["recent_projects"]>[number];

/**
 * The projects worth naming aloud, newest first, at most three.
 *
 * Deduplicated by STREET. A firm that pulled three permits on one building —
 * LMDG has three at 650 W 41st Avenue — would otherwise produce "I saw the
 * permit for 650 W 41st Avenue, and 650 W 41st Avenue before that", which
 * reads as a mail merge that has gone wrong. One address per building.
 */
function projects(c: CrmContactDetail): Project[] {
  const seen = new Set<string>();
  const out: Project[] = [];
  for (const p of c.recent_projects ?? []) {
    if (!p.address) continue;
    const key = street(p.address).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(p);
  }
  return out;
}

/** Re-wrap a sentence to fit a fixed-width block, indented after the first line. */
function wrap(text: string, width: number, indent: string): string {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let line = "";
  for (const w of words) {
    if (line && (line + " " + w).length > width) {
      lines.push(line);
      line = w;
    } else {
      line = line ? line + " " + w : w;
    }
  }
  if (line) lines.push(line);
  return lines.join("\n" + indent);
}

/**
 * The opening clause that proves the message was written for this firm.
 *
 * A real address beats a permit count outright — "I saw the permit for 2550 E
 * 3rd Avenue" cannot be sent to anyone else, and a count can. Roughly 39% of
 * firms have no project data, so the aggregate is kept as the fallback rather
 * than leaving a gap.
 */
function evidence(c: CrmContactDetail): string {
  const ps = projects(c);
  const n = c.permits_as_applicant || c.permits_total || 0;
  if (ps.length === 0) {
    return `${n} permits in ${cities(c.cities)}, mostly ${work(c)}`;
  }
  const dated = when(ps[0].issued);
  const first = `the permit for ${street(ps[0].address)}${dated ? ` in ${dated}` : ""}`;
  if (ps.length === 1) return first;
  return `${first}, and ${street(ps[1].address)} before that`;
}

function work(c: CrmContactDetail): string {
  const t = (c.top_use_category ?? "").toLowerCase();
  if (t.includes("laneway")) return "laneway and coach houses";
  if (t.includes("multiple")) return "multi-unit residential";
  if (t.includes("single")) return "single-family work";
  if (t.includes("duplex") || t.includes("townhouse")) return "small multi-family";
  return t || "residential work";
}

export function draftFor(c: CrmContactDetail): { kind: string; body: string } {
  const n = c.permits_as_applicant || c.permits_total || 0;
  const recent = projects(c);
  const where = cities(c.cities);
  const what = work(c);
  const val = money(c.value_as_applicant);

  // ── Tier A — talking points for a call, not an email ────────────────────
  if (c.tier === "A") {
    return {
      kind: "Call — talking points",
      body:
`${c.name}${c.phone ? ` · ${c.phone}` : ""}

WHY THEM
  ${n} permits as applicant${val ? `, ${val} of project value` : ""} in ${where}.
  Mostly ${what}. They design and hand off — they are not a competitor.
${recent.length ? `
THEIR RECENT WORK — name one, not all three
${recent.map((p) => `  · ${street(p.address)}${when(p.issued) ? ` — ${when(p.issued)}` : ""}${p.work ? `, ${p.work.toLowerCase()}` : ""}`).join("\n")}
` : ""}
OPENING
  "${wrap(`I'm Amir with BRIO Construction in North Vancouver. I build the kind of ${what} you're designing — I saw ${evidence(c)}.`, 68, "   ")}"

WHAT TO ASK — not what to pitch
  · When a client of yours needs a builder, how does that usually happen?
  · Do you have builders you refer to, or does the owner arrive with one?
  · What makes a builder easy to work with from your side?

THE ONLY THING TO OFFER
  To be a second name they can give a client. Not to replace anyone.
  Most designers want two or three reliable builders, not one.

CLOSE
  Ask what would be useful to send. Then send exactly that, and nothing else.`,
    };
  }

  // ── Tier B — email naming their actual work ─────────────────────────────
  if (c.tier === "B") {
    return {
      kind: "Email",
      body:
`Subject: ${what.charAt(0).toUpperCase() + what.slice(1)} in ${where.split(",")[0]}

Hi,

I'm Amir Omidvar — I run BRIO Construction, a licensed residential builder in
North Vancouver.

${wrap(`I came across ${c.name} through building permit records — I saw ${evidence(c)}. That's the work we build, so I wanted to introduce myself.`, 76, "")}

We're a general contractor, not a design firm — so this isn't a pitch to compete
with you. Most of the designers we work with keep two or three builders they can
point a client toward, and I'd like to be one of yours.

If it's useful, I can send a short summary of recent projects and our licensing
and insurance. If not, no need to reply.

Amir Omidvar
BRIO Construction Inc. · 1 (236) 706-9686
builtbybrio.com`,
    };
  }

  // ── Tier C/D — short, still specific ────────────────────────────────────
  return {
    kind: "Email — short",
    body:
`Subject: Builder introduction — ${where.split(",")[0]}

Hi,

${wrap(`Amir Omidvar, BRIO Construction — licensed residential builder in North Vancouver. I came across ${c.name} in ${where} permit records — I saw ${evidence(c)}.`, 76, "")}

We build ${what}. If you ever need a builder to point a client toward, I'd like
to be on that list. Happy to send credentials if useful.

Amir Omidvar
BRIO Construction Inc. · 1 (236) 706-9686`,
  };
}

export function CrmDraft({ contact }: { contact: CrmContactDetail }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const d = draftFor(contact);

  async function copy() {
    try {
      await navigator.clipboard.writeText(d.body);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch { /* clipboard blocked — the text is on screen to select */ }
  }

  return (
    <section className="mt-4 rounded border border-slate-200 bg-white">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-3 py-2.5 text-left"
      >
        <span className="text-sm font-medium">
          Draft opener{" "}
          <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
            {d.kind}
          </span>
        </span>
        <span className="text-xs text-slate-500">{open ? "Hide" : "Show"}</span>
      </button>

      {open && (
        <div className="border-t border-slate-200 p-3">
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-3 font-mono text-xs leading-relaxed text-slate-800">
{d.body}
          </pre>
          <div className="mt-2 flex items-center gap-2">
            <button
              onClick={copy}
              className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
            >
              {copied ? "Copied" : "Copy"}
            </button>
            <p className="text-xs text-slate-500">
              Read it before sending — every address and number in it comes from
              their permit record, so it is wrong the moment that record is.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
