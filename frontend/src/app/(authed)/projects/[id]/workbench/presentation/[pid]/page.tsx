import Link from "next/link";
import { loadOr } from "@/lib/loadOr";
import { LoadFailed } from "@/components/LoadFailed";

import { api } from "@/lib/api";
import { getPresentation, listEstimateVersions } from "@/lib/workbench";
import { ShareLinkButton } from "../ShareLinkButton";
import { BudgetChart, ScheduleGantt } from "./PresentationVisuals";

const money = (n: number | undefined | null) =>
  typeof n === "number"
    ? n.toLocaleString("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 })
    : "—";

const DISC_LABEL: Record<string, string> = {
  architectural: "Architectural",
  structural: "Structural",
  stormwater: "Storm Water",
  mechanical: "Mechanical",
  electrical: "Electrical",
  plumbing: "Plumbing",
  other: "Other",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold" style={{ color: "#1D3146" }}>{title}</h2>
      {children}
    </section>
  );
}

export default async function PresentationViewer({
  params,
}: {
  params: Promise<{ id: string; pid: string }>;
}) {
  const { id, pid } = await params;
  const [pres, est] = await Promise.all([
    getPresentation(id, pid),
    loadOr("estimate versions", listEstimateVersions(id)),
  ]);
  // Unknown is not "up to date": without the list, no stale flag is shown, and
  // the page says it couldn't check (estFailed below).
  const estimates = est.data ?? [];
  const estFailed = est.failed;
  const p = pres.payload ?? {};
  const activeEstId = estimates.find((e) => e.is_active)?.id ?? null;
  const stale =
    !!activeEstId && !!pres.based_on_estimate_id && pres.based_on_estimate_id !== activeEstId;

  // Presigned URLs for the drawing PDFs (server-side; embedded full-page below).
  const drawings = p.drawings ?? [];
  const drawingUrls = await Promise.all(
    drawings.map((d) =>
      api<{ download_url: string }>(`/documents/${d.id}/download`)
        .then((r) => r.download_url)
        .catch(() => null),
    ),
  );

  const b = p.budget;
  const ownerDecisions = (p.ownerDecisions ?? []).slice().sort((a, z) =>
    (a.decideByDate ?? "9999").localeCompare(z.decideByDate ?? "9999"),
  );
  const ourAssumptions = p.ourAssumptions ?? [];

  return (
    <div className="mx-auto max-w-3xl space-y-8 pb-16">
      <div className="flex items-center justify-between">
        <Link href={`/projects/${id}/workbench/presentation`} className="text-sm text-slate-500">
          ← All presentations
        </Link>
        <div className="flex items-center gap-3">
          <ShareLinkButton projectId={id} presentationId={pid} />
          <a
            href={`/api/workbench/presentation-pdf/${id}/${pid}`}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-md px-3 py-1.5 text-sm font-medium text-white"
            style={{ backgroundColor: "#0068A0" }}
          >
            ⤓ Download proposal PDF
          </a>
          <span className="text-xs uppercase tracking-wide text-slate-400">
            {pres.audience} · v{pres.version_number} · {pres.status}
          </span>
        </div>
      </div>

      {estFailed && <LoadFailed what="the estimates, so whether this proposal is out of date wasn't checked" small />}
      {stale && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          ⚠ The active estimate has changed since this deck was generated. Regenerate before sending it to the client.
        </div>
      )}
      {(p.provenance?.stale?.length ?? 0) > 0 && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          ⚠ These downstream artifacts may be out of date with the estimate: {p.provenance!.stale!.join(", ")}.
        </div>
      )}

      {/* Cover */}
      <header className="space-y-2 border-b border-slate-200 pb-6">
        <p className="text-xs font-medium uppercase tracking-widest" style={{ color: "#0068A0" }}>
          BRIO Construction · Client Presentation
        </p>
        <h1 className="text-2xl font-bold leading-snug" style={{ color: "#1D3146" }}>
          {p.headline ?? p.project?.name ?? "Project Presentation"}
        </h1>
        <p className="text-sm text-slate-600">
          {p.project?.name}
          {p.project?.address ? ` — ${p.project.address}` : ""}
          {p.client?.name ? ` · Prepared for ${p.client.name}` : ""}
        </p>
      </header>

      {p.overview && (
        <Section title="Overview">
          <div className="whitespace-pre-line text-sm leading-relaxed text-slate-700">{p.overview}</div>
        </Section>
      )}

      {/* Budget */}
      {b && (
        <Section title="Project Budget">
          <BudgetChart categories={b.categories.filter((c) => !c.optional).map((c) => ({ name: c.name, value: c.displayTotal }))} />
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {b.categories.map((c) => (
              <div key={c.name} className="rounded-md border border-slate-200 p-3">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-medium text-slate-800">{c.name}</span>
                  <span className="text-sm font-semibold" style={{ color: "#1D3146" }}>{money(c.displayTotal)}</span>
                </div>
                {c.optional && <span className="text-xs text-slate-400">optional</span>}
                {(() => {
                  const inc = (c.includes ?? []).filter((x) => !/contingency/i.test(x));
                  return inc.length > 0 ? (
                    <div className="mt-1.5">
                      <div className="text-[10px] font-medium uppercase tracking-wide text-slate-400">Includes</div>
                      <ul className="mt-0.5 list-disc space-y-0.5 pl-4 text-xs text-slate-500">
                        {inc.map((it, j) => <li key={j}>{it}</li>)}
                      </ul>
                    </div>
                  ) : null;
                })()}
              </div>
            ))}
          </div>
          <table className="mt-2 w-full text-sm">
            <tbody>
              <tr><td className="py-1 text-slate-600">Subtotal</td><td className="py-1 text-right">{money(b.subtotal)}</td></tr>
              {b.allowances ? <tr><td className="py-1 text-slate-600">Allowances</td><td className="py-1 text-right">{money(b.allowances)}</td></tr> : null}
              <tr><td className="py-1 text-slate-600">GST (5%)</td><td className="py-1 text-right">{money(b.gst)}</td></tr>
              <tr className="border-t border-slate-300"><td className="py-1.5 font-semibold">Project Total</td><td className="py-1.5 text-right font-bold" style={{ color: "#0068A0" }}>{money(b.total)}</td></tr>
            </tbody>
          </table>
          <p className="text-xs text-slate-400">
            Derived from a detailed, calculated estimate{b.detailLevel ? ` (${b.detailLevel.replace(/_/g, " ")})` : ""}
            {b.validDays ? ` · valid ${b.validDays} days` : ""}. Figures are inclusive of GST.
          </p>
        </Section>
      )}

      {/* Why the estimate changed vs. the earlier figure the client saw */}
      {p.estimateChange && (
        <Section title="Why This Differs From Our Earlier Estimate">
          <div className="rounded-md border border-amber-200 bg-amber-50 p-4">
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
              <div>
                <p className="text-xs text-slate-500">Earlier estimate{p.estimateChange.priorDate ? ` (${p.estimateChange.priorDate})` : ""}</p>
                <p className="text-lg font-semibold text-slate-500 line-through">{money(p.estimateChange.priorTotal)}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Current estimate</p>
                <p className="text-lg font-bold" style={{ color: "#1D3146" }}>{money(p.estimateChange.currentTotal)}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Difference</p>
                <p className="text-lg font-semibold" style={{ color: "#b26a00" }}>+{money(p.estimateChange.delta)}</p>
              </div>
            </div>
            {p.estimateChange.explanation && (
              <p className="mt-3 text-sm leading-relaxed text-slate-700">{p.estimateChange.explanation}</p>
            )}
          </div>
        </Section>
      )}

      {/* High-cost items */}
      {(p.highCostItems?.length ?? 0) > 0 && (
        <Section title="Major Work Areas — Technical &amp; Logistic Notes">
          <p className="text-sm text-slate-600">The largest work packages and what makes them technically or logistically involved.</p>
          <div className="space-y-2">
            {p.highCostItems!.map((h) => (
              <div key={h.categoryName} className="rounded-md border border-slate-200 p-3">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-semibold text-slate-800">{h.categoryName}</span>
                  <span className="text-sm font-semibold" style={{ color: "#1D3146" }}>{money(h.displayTotal)}</span>
                </div>
                {h.note && <p className="mt-1 text-sm text-slate-600">{h.note}</p>}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Scope */}
      {(p.scopeSections?.length ?? 0) > 0 && (
        <Section title="Scope of Work">
          <div className="space-y-3">
            {p.scopeSections!.map((s, i) => (
              <div key={i}>
                <h3 className="text-sm font-semibold text-slate-800">{s.title}</h3>
                <p className="mt-0.5 text-sm text-slate-600">{s.clientDescription}</p>
                {(s.featureDrawingDisciplines?.length ?? 0) > 0 && (
                  <p className="mt-1 text-xs text-slate-400">
                    See: {s.featureDrawingDisciplines!.map((d) => DISC_LABEL[d] ?? d).join(", ")} drawings
                  </p>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Decisions From the Owner — only selections that gate construction/schedule */}
      {ownerDecisions.length > 0 && (
        <Section title="Decisions From the Owner">
          <p className="text-sm text-slate-600">
            These are the selections we need <em>from you</em> — they gate specific work and carry lead times. To hold the schedule, please confirm each by the date shown.
          </p>
          <div className="space-y-2">
            {ownerDecisions.map((a, i) => (
              <div key={i} className="rounded-md border border-slate-200 p-3" style={{ borderLeft: "3px solid #0068A0" }}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-semibold text-slate-800">{a.title}</span>
                  {a.decideByDate && (
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                      decide by {a.decideByDate}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm text-slate-600"><span className="font-medium">Decide:</span> {a.whatToDecide}</p>
                {a.affectsSchedule && <p className="mt-0.5 text-sm text-slate-500"><span className="font-medium">Affects:</span> {a.affectsSchedule}</p>}
                {typeof a.leadTimeWeeks === "number" && (
                  <p className="mt-0.5 text-xs text-slate-400">~{a.leadTimeWeeks}-week lead time</p>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Our Assumptions — carried assumptions we verify on site; change orders if wrong */}
      {ourAssumptions.length > 0 && (
        <Section title="Our Assumptions">
          <p className="text-sm text-slate-600">
            To price the work we&apos;ve assumed the following. We verify each during site visits, excavation, and demolition — <span className="font-medium">if a condition turns out different, it would be handled as a change order</span>. We&apos;re flagging them now so there are no surprises.
          </p>
          <div className="space-y-2">
            {ourAssumptions.map((a, i) => (
              <div key={i} className="rounded-md border border-slate-200 p-3" style={{ borderLeft: "3px solid #c98a1a" }}>
                <span className="text-sm font-semibold text-slate-800">{a.title}</span>
                <p className="mt-1 text-sm text-slate-600"><span className="font-medium">We assume:</span> {a.assumption}</p>
                {a.ifWrong && <p className="mt-0.5 text-sm text-slate-500"><span className="font-medium">If not:</span> {a.ifWrong}</p>}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Schedule */}
      {(p.schedule?.present || p.schedule?.narrative || p.schedule?.projectStart) && (
        <Section title="Schedule">
          {p.schedule.narrative && <p className="text-sm text-slate-700">{p.schedule.narrative}</p>}
          <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
            {p.schedule.projectStart && <Fact label="Start" value={p.schedule.projectStart} />}
            {p.schedule.finishP50 && <Fact label="Target finish (P50)" value={p.schedule.finishP50} />}
            {p.schedule.finishP80 && <Fact label="Conservative (P80)" value={p.schedule.finishP80} />}
          </div>
          {(p.schedule.activities?.length ?? 0) > 0 && (
            <ScheduleGantt
              activities={p.schedule.activities!}
              milestones={p.schedule.milestones ?? undefined}
              start={p.schedule.projectStart ?? undefined}
              finish={p.schedule.finishP50 ?? undefined}
            />
          )}
          {(p.schedule.activities?.length ?? 0) > 0 && (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b-2 border-slate-300 text-left text-[11px] uppercase tracking-wide text-slate-400">
                    <th className="py-1.5 pr-2 font-medium">Task</th>
                    <th className="py-1.5 pr-2 font-medium">Phase</th>
                    <th className="py-1.5 pr-2 text-right font-medium">Days</th>
                    <th className="py-1.5 pr-2 font-medium">Start</th>
                    <th className="py-1.5 font-medium">Finish</th>
                  </tr>
                </thead>
                <tbody>
                  {p.schedule.activities!.filter((a) => !a.milestone).map((a, i) => (
                    <tr key={i} className={`border-b border-slate-100 ${a.critical ? "font-medium text-slate-800" : "text-slate-600"}`}>
                      <td className={`py-1 pr-2 ${a.critical ? "border-l-2 pl-1.5" : ""}`} style={a.critical ? { borderColor: "#0068A0" } : undefined}>{a.name}</td>
                      <td className="py-1 pr-2 text-slate-500">{a.phase}</td>
                      <td className="py-1 pr-2 text-right">{Math.round(a.duration || 0)}</td>
                      <td className="py-1 pr-2 whitespace-nowrap text-slate-500">{a.start}</td>
                      <td className="py-1 whitespace-nowrap text-slate-500">{a.finish}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="text-xs text-slate-400">
            Working days (Mon–Fri), a most-likely estimate with a P80 confidence buffer. The schedule assumes permits are issued at the project start — permitting is a separate pre-construction step, not part of the build duration above.
          </p>
        </Section>
      )}

      {/* Cash flow */}
      {(p.cashFlow?.present || p.cashFlow?.narrative) && (
        <Section title="Cash Flow Requirement">
          {p.cashFlow?.narrative && <p className="text-sm text-slate-700">{p.cashFlow.narrative}</p>}
          {(p.budget?.paymentSchedule?.length ?? 0) > 0 && (
            <table className="w-full text-sm">
              <tbody>
                {p.budget!.paymentSchedule!.map((ps, i) => (
                  <tr key={i} className="border-t border-slate-100">
                    <td className="py-1 text-slate-700">{ps.milestone ?? ps.label ?? `Payment ${i + 1}`}</td>
                    <td className="py-1 text-right text-slate-600">{ps.percent ? `${ps.percent}%` : ""}</td>
                    <td className="py-1 text-right">{money(ps.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}

      {/* Drawings — full-page source PDFs */}
      {drawings.length > 0 && (
        <Section title="Drawings">
          <div className="space-y-4">
            {drawings.map((d, i) => (
              <div key={d.id} className="rounded-md border border-slate-200">
                <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
                  <span className="text-sm font-medium text-slate-800">{d.filename}</span>
                  <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{DISC_LABEL[d.discipline] ?? d.discipline}</span>
                </div>
                {drawingUrls[i] && d.filename.toLowerCase().endsWith(".pdf") ? (
                  <iframe src={drawingUrls[i]!} title={d.filename} className="h-[480px] w-full" />
                ) : drawingUrls[i] ? (
                  <a href={drawingUrls[i]!} target="_blank" rel="noopener noreferrer" className="block px-3 py-4 text-sm" style={{ color: "#0068A0" }}>Open drawing ↗</a>
                ) : (
                  <p className="px-3 py-4 text-sm text-slate-400">Preview unavailable.</p>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {(p.exclusions?.length ?? 0) > 0 && (
        <Section title="What's Not Included (Exclusions)">
          <p className="text-sm text-slate-600">
            The price above does <span className="font-medium">not</span> include the following. If any becomes required, it would be a separate change order or extra cost — listed here so it&apos;s clear up front.
          </p>
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-700">{p.exclusions!.map((e, i) => <li key={i}>{e}</li>)}</ul>
        </Section>
      )}

      {(p.assumptions?.length ?? 0) > 0 && (
        <Section title="General Assumptions">
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-600">{p.assumptions!.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </Section>
      )}

      {p.closingNote && (
        <div className="border-t border-slate-200 pt-6 text-sm italic text-slate-600">{p.closingNote}</div>
      )}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 p-2">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="font-medium text-slate-800">{value}</p>
    </div>
  );
}
