"use client";

import { useEffect, useState } from "react";

import { editProposalText, proposalStatus, sendProposal, withdrawProposal, type ProposalStatus } from "@/lib/proposalPortal";
import { errorMessage } from "@/lib/workspaceApi";

/** Client › Proposal: which version is in the client's portal, and whether
 *  they accepted it. Sending posts a short note in the client thread and
 *  emails them that it's there; accepting never changes a contract. */

const money = (n: number | null) => (n == null ? "—" : n.toLocaleString("en-CA", { style: "currency", currency: "CAD" }));
const when = (s: string | null) =>
  s ? new Date(s).toLocaleString("en-CA", { dateStyle: "medium", timeStyle: "short", timeZone: "America/Vancouver" }) : "";

export function PortalPanel({ projectId }: { projectId: string }) {
  const [rows, setRows] = useState<ProposalStatus[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<{ id: string; headline: string; overview: string; closingNote: string } | null>(null);

  useEffect(() => { proposalStatus(projectId).then(setRows, (e) => setErr(errorMessage(e))); }, [projectId]);

  async function act(fn: () => Promise<ProposalStatus[]>, confirmText: string) {
    if (!window.confirm(confirmText)) return;
    setBusy(true);
    setErr(null);
    try { setRows(await fn()); } catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
  }

  if (!rows) return err ? <p className="text-sm text-red-700">{err}</p> : null;
  if (rows.length === 0) return null;
  const accepted = rows.find((r) => r.accepted);
  const live = rows.find((r) => r.in_portal || r.accepted) ?? rows[rows.length - 1];
  const steps: [string, boolean][] = [
    ["Built", true], ["Sent", !!live.sent_at], [`Opened${live.open_count ? ` · ${live.open_count}×` : ""}`, live.open_count > 0],
    ["Accepted → contract", !!live.accepted],
  ];
  const stale = rows[rows.length - 1].stale;

  return (
    <section className="rounded border border-slate-200 bg-white">
      <h2 className="px-4 pt-3 text-sm font-semibold uppercase tracking-wide text-slate-700">Client portal · v{live.version_number}</h2>
      <ol className="mx-4 mt-2 flex flex-wrap" aria-label="Where this proposal is">
        {steps.map(([label, done]) => (
          <li key={label} className={`min-w-28 flex-1 border-t-4 px-2 py-1.5 text-xs ${done ? "border-slate-900 font-semibold" : "border-slate-200 text-slate-500"}`}>{label}</li>
        ))}
      </ol>
      {stale && !accepted && (
        <p className="mx-4 mt-2 rounded border border-orange-700 bg-orange-50 px-3 py-2 text-sm text-orange-950">
          Estimate v{stale.estimate_version} is active now and this proposal was built from an earlier one.
          Price here {money(rows[rows.length - 1].total)}; v{stale.estimate_version} {money(stale.estimate_total)}.
          Rebuild it with &ldquo;Generate client presentation&rdquo; above (about $0.55 of AI).
        </p>
      )}
      {accepted?.accepted && (
        <p className="mx-4 mt-2 rounded border border-green-700 px-3 py-2 text-sm" role="status">
          <b className="text-green-800">Accepted:</b> v{accepted.version_number} at {money(accepted.accepted.total)} incl. GST,
          by {accepted.accepted.typed_name}, {when(accepted.accepted.accepted_at)}
          {accepted.accepted.ip && <span className="text-xs text-slate-500"> · from {accepted.accepted.ip}</span>}.
          Prepare the contract on Money › Contract; accepting didn&apos;t create one.
        </p>
      )}
      <table className="mt-2 w-full text-sm">
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-slate-100">
              <td className="px-4 py-2 font-medium">v{r.version_number}</td>
              <td className="px-3 py-2">{money(r.total)}
                {r.change_from_previous ? <div className="text-xs text-slate-500">{r.change_from_previous > 0 ? "+" : "−"}{money(Math.abs(r.change_from_previous))} vs v{r.version_number - 1}</div> : null}
              </td>
              <td className="px-3 py-2 text-xs text-slate-600">
                {r.accepted ? "accepted"
                  : r.in_portal ? <>in the portal since {when(r.sent_at)}{r.valid_until && <> · price valid until {r.valid_until}{r.expired && " (expired)"}</>}</>
                  : r.withdrawn_at ? "withdrawn" : "not sent"}
              </td>
              <td className="px-3 py-2 text-right whitespace-nowrap">
                <a className="mr-3 text-xs text-blue-700 underline" href={`/api/workbench/presentation-pdf/${projectId}/${r.id}`} target="_blank" rel="noopener noreferrer">Preview as client</a>
                {!accepted && !r.in_portal && (
                  <button type="button" className="mr-3 text-xs underline"
                    onClick={() => setEditing({ id: r.id, headline: r.headline ?? "", overview: r.overview ?? "", closingNote: r.closingNote ?? "" })}>Edit words</button>
                )}
                {!accepted && (r.in_portal ? (
                  <button type="button" disabled={busy} className="text-sm text-slate-700 underline"
                    onClick={() => act(() => withdrawProposal(projectId, r.id), `Take v${r.version_number} out of the client's portal?`)}>
                    Withdraw
                  </button>
                ) : (
                  <button type="button" disabled={busy}
                    className="rounded bg-[var(--color-brio-primary)] px-3 py-1 text-sm font-medium text-white disabled:opacity-50"
                    onClick={() => act(() => sendProposal(projectId, r.id),
                      `Send v${r.version_number} to the client's portal? They get a message and an email that it's there, and can accept it.`)}>
                    Send to client portal
                  </button>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing && (
        <div className="space-y-2 border-t border-slate-100 px-4 py-3 text-sm">
          <b>Edit the words of v{rows.find((r) => r.id === editing.id)?.version_number}</b>
          {(["headline", "overview", "closingNote"] as const).map((k) => (
            <label key={k} className="block">
              {k === "headline" ? "Headline" : k === "overview" ? "Overview" : "Closing note"}
              <textarea rows={k === "headline" ? 2 : 4} value={editing[k]} onChange={(e) => setEditing({ ...editing, [k]: e.target.value })}
                className="mt-1 block w-full rounded border border-slate-300 px-2 py-1 text-sm" />
            </label>
          ))}
          <div className="flex gap-2">
            <button type="button" disabled={busy} className="rounded bg-[var(--color-brio-primary)] px-3 py-1 text-sm font-medium text-white"
              onClick={async () => {
                setBusy(true); setErr(null);
                try { setRows(await editProposalText(projectId, editing.id, { headline: editing.headline, overview: editing.overview, closingNote: editing.closingNote })); setEditing(null); }
                catch (e) { setErr(errorMessage(e)); } finally { setBusy(false); }
              }}>Save words</button>
            <button type="button" className="text-sm underline" onClick={() => setEditing(null)}>Cancel</button>
          </div>
          <p className="text-xs text-slate-500">Prices and scope come from the estimate; change those there and rebuild.</p>
        </div>
      )}
      {err && <p className="px-4 py-2 text-sm text-red-700">{err}</p>}
      <p className="px-4 py-2 text-xs text-slate-500">
        One version at a time is in the portal. The client accepts by typing their name; we record when, from where and the price they saw.
      </p>
    </section>
  );
}
