"use client";

import { useState } from "react";
import { updateChangeOrder, type ChangeOrder } from "@/lib/changeOrders";

/**
 * Edit a change order while it is still undecided.
 *
 * Line items are the point. A generated change order infers its quantities from
 * a photo and a sentence, so correcting "100 LF of wall" is the normal edit, not
 * an edge case. A HAND-WRITTEN one starts with none, and until 2026-09-27 could
 * never acquire any: the grid and its "+ Add a line" button were both inside the
 * branch that only rendered once a line existed. The table is now always
 * present, so a change order typed from scratch can be broken into sections. The total is derived from the rows rather than typed, because a
 * total that disagrees with the breakdown beneath it is worse than showing no
 * breakdown at all: it walks a client through figures that do not add up.
 *
 * Assumptions and exclusions matter just as much and were previously read-only,
 * which is how CO-1 ended up billing three items its own exclusions said were
 * "not part of this change order": the lines were added here, the exclusions
 * could not be. With lump-sum pricing on the client document these two lists
 * ARE the scope, so they have to be editable next to the lines that create them.
 *
 * The server refuses an edit once the order is approved, rejected or void, which
 * is what makes an approved change order worth anything as evidence. This form
 * is only offered for draft and pending.
 */
type Row = { description: string; qty: string; unit: string; unit_cost: string; amount: string };

const money = (n: number) =>
  n.toLocaleString("en-CA", { style: "currency", currency: "CAD" });

/** One entry per non-blank line; a leading bullet or dash is stripped so
 *  pasting from a document does not leave "- " inside the text. */
const toList = (raw: string): string[] =>
  raw
    .split("\n")
    .map((l) => l.replace(/^\s*[-•*]\s*/, "").trim())
    .filter(Boolean);

export default function EditChangeOrder({
  projectId,
  co,
  onSaved,
  onCancel,
}: {
  projectId: string;
  co: ChangeOrder;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(co.title);
  const [description, setDescription] = useState(co.description ?? "");
  const [days, setDays] = useState(String(co.schedule_impact_days ?? 0));
  const [rows, setRows] = useState<Row[]>(
    (co.detail?.line_items ?? []).map((li) => ({
      description: li.description ?? "",
      qty: li.qty != null ? String(li.qty) : "",
      unit: li.unit ?? "",
      unit_cost: li.unit_cost != null ? String(li.unit_cost) : "",
      amount: li.amount != null ? String(li.amount) : "",
    })),
  );
  // One entry per line. A textarea rather than a row editor: these are
  // sentences, they get rewritten wholesale far more often than reordered, and
  // a list of eight paragraph-length exclusions is unusable as a row grid.
  const [assumptions, setAssumptions] = useState((co.detail?.assumptions ?? []).join("\n"));
  const [exclusions, setExclusions] = useState((co.detail?.exclusions ?? []).join("\n"));
  // No breakdown to edit (a hand-written order): fall back to a single total.
  const [flatCost, setFlatCost] = useState(String(co.cost_impact ?? 0));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const hasRows = rows.length > 0;
  const total = rows.reduce((s, r) => s + (Number(r.amount) || 0), 0);

  // A line that is also named in the exclusions is a document that contradicts
  // itself in front of a client. CO-1 shipped with three. Flagged, never
  // auto-resolved: only a person knows which of the two is the mistake.
  const clash = toList(exclusions)
    .map((x) => {
      const head = x.split(/[–—(]/)[0].trim().toLowerCase();
      if (head.length < 8) return null;
      const hit = rows.find((r) => {
        const d = r.description.trim().toLowerCase();
        return d.length > 7 && (d.includes(head) || head.includes(d));
      });
      return hit ? hit.description.trim() : null;
    })
    .filter((v): v is string => v !== null);

  function setRow(i: number, patch: Partial<Row>) {
    setRows((prev) => {
      const next = [...prev];
      const row = { ...next[i], ...patch };
      // Typing a quantity or a unit price fills the amount in. Editing the
      // amount directly still wins, so an odd lump-sum line stays possible.
      if (("qty" in patch || "unit_cost" in patch) && row.qty && row.unit_cost) {
        row.amount = String(
          Math.round((Number(row.qty) || 0) * (Number(row.unit_cost) || 0) * 100) / 100,
        );
      }
      next[i] = row;
      return next;
    });
  }

  async function save() {
    if (!title.trim()) {
      setErr("A change order needs a title.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await updateChangeOrder(projectId, co.id, {
        title: title.trim(),
        description: description.trim(),
        schedule_impact_days: Number(days) || 0,
        detail: {
          ...co.detail,
          assumptions: toList(assumptions),
          exclusions: toList(exclusions),
          ...(hasRows
            ? {
                line_items: rows
                  .filter((r) => r.description.trim())
                  .map((r) => ({
                    description: r.description.trim(),
                    qty: r.qty === "" ? undefined : Number(r.qty),
                    unit: r.unit.trim() || undefined,
                    unit_cost: r.unit_cost === "" ? undefined : Number(r.unit_cost),
                    amount: Number(r.amount) || 0,
                  })),
              }
            : {}),
        },
        // Only meaningful with no breakdown — with rows the server derives the
        // total from them and ignores anything sent here.
        ...(hasRows ? {} : { cost_impact: Number(flatCost) || 0 }),
      });
      onSaved();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const input = "w-full rounded border border-slate-300 px-2 py-1 text-xs";

  return (
    <div className="mt-3 space-y-3 rounded border border-blue-200 bg-blue-50/40 p-3">
      <label className="block text-xs text-slate-600">
        Title
        <input value={title} onChange={(e) => setTitle(e.target.value)} disabled={busy}
               className={input + " mt-1 text-sm"} />
      </label>

      <label className="block text-xs text-slate-600">
        Description
        <textarea value={description} onChange={(e) => setDescription(e.target.value)}
                  disabled={busy} rows={3} className={input + " mt-1"} />
      </label>

      {/* The table renders whether or not lines exist yet. It used to be hidden
          behind `hasRows`, which put "+ Add a line" inside the branch that only
          appears once a line already exists — so a change order created by hand
          could never gain its first one, and neither the grid nor the prompt
          editor could rescue it. The empty state is the entry point now. */}
      <div>
        <div className="text-xs font-medium text-slate-600">Line items</div>
        {hasRows ? (
          <div className="overflow-x-auto">
            <table className="mt-1 w-full min-w-[36rem] text-xs">
              <thead className="text-slate-500">
                <tr>
                  <th className="text-left font-medium">Item</th>
                  <th className="w-16 text-right font-medium">Qty</th>
                  <th className="w-14 text-left font-medium">Unit</th>
                  <th className="w-24 text-right font-medium">Unit $</th>
                  <th className="w-24 text-right font-medium">Amount</th>
                  <th className="w-6" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="align-top">
                    <td className="pr-1 py-0.5">
                      <input value={r.description} disabled={busy}
                             onChange={(e) => setRow(i, { description: e.target.value })}
                             className={input} />
                    </td>
                    <td className="px-1 py-0.5">
                      <input value={r.qty} disabled={busy} type="number"
                             onChange={(e) => setRow(i, { qty: e.target.value })}
                             className={input + " text-right"} />
                    </td>
                    <td className="px-1 py-0.5">
                      <input value={r.unit} disabled={busy}
                             onChange={(e) => setRow(i, { unit: e.target.value })}
                             className={input} />
                    </td>
                    <td className="px-1 py-0.5">
                      <input value={r.unit_cost} disabled={busy} type="number" step="0.01"
                             onChange={(e) => setRow(i, { unit_cost: e.target.value })}
                             className={input + " text-right"} />
                    </td>
                    <td className="px-1 py-0.5">
                      <input value={r.amount} disabled={busy} type="number" step="0.01"
                             onChange={(e) => setRow(i, { amount: e.target.value })}
                             className={input + " text-right"} />
                    </td>
                    <td className="py-0.5 text-center">
                      <button type="button" disabled={busy}
                              onClick={() => setRows((p) => p.filter((_, k) => k !== i))}
                              title="Remove this line"
                              className="text-slate-400 hover:text-red-600">×</button>
                    </td>
                  </tr>
                ))}
                <tr className="border-t-2 border-slate-300 font-medium">
                  <td className="py-1" colSpan={4}>Total, pre-tax</td>
                  <td className="py-1 text-right tabular-nums">{money(total)}</td>
                  <td />
                </tr>
              </tbody>
            </table>
          </div>
        ) : (
          <p className="mt-1 text-[11px] text-slate-500">
            No breakdown yet. Add lines to price this change order section by
            section — the total is then the sum of them. Leave it empty to carry
            a single figure instead.
          </p>
        )}
        <button type="button" disabled={busy}
                onClick={() => setRows((p) => [...p, { description: "", qty: "", unit: "", unit_cost: "", amount: "" }])}
                className="mt-1 text-xs font-medium text-blue-600 hover:underline">
          + Add a line
        </button>
        {hasRows && (
          <div className="mt-1 text-[11px] text-slate-500">
            The total is the sum of these lines. It is not typed separately.
          </div>
        )}
      </div>

      {/* Only meaningful with no breakdown. With lines the server derives the
          total from them and ignores anything typed here, so showing both
          would offer a number that silently does nothing. */}
      {!hasRows && (
        <label className="block text-xs text-slate-600">
          Cost impact (pre-tax)
          <input value={flatCost} onChange={(e) => setFlatCost(e.target.value)} disabled={busy}
                 type="number" step="0.01" className={input + " mt-1 w-40 text-right"} />
        </label>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-xs text-slate-600">
          Assumptions
          <span className="ml-1 text-slate-400">(one per line)</span>
          <textarea value={assumptions} onChange={(e) => setAssumptions(e.target.value)}
                    disabled={busy} rows={6} className={input + " mt-1"} />
        </label>
        <label className="block text-xs text-slate-600">
          Exclusions
          <span className="ml-1 text-slate-400">(one per line)</span>
          <textarea value={exclusions} onChange={(e) => setExclusions(e.target.value)}
                    disabled={busy} rows={6} className={input + " mt-1"} />
        </label>
      </div>
      {clash.length > 0 && (
        <div className="rounded border border-amber-300 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800">
          <b>These are billed and excluded at the same time:</b>
          <ul className="mt-0.5 list-disc pl-4">
            {clash.map((c) => <li key={c}>{c}</li>)}
          </ul>
          Remove the exclusion, or remove the line.
        </div>
      )}

      <label className="block text-xs text-slate-600">
        Schedule impact (days ±)
        <input value={days} onChange={(e) => setDays(e.target.value)} disabled={busy}
               type="number" className={input + " mt-1 w-32 text-right"} />
      </label>

      {err && (
        <div className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">{err}</div>
      )}

      <div className="flex items-center gap-3">
        <button onClick={save} disabled={busy}
                className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white disabled:opacity-60">
          {busy ? "Saving…" : "Save changes"}
        </button>
        <button onClick={onCancel} disabled={busy}
                className="text-xs text-slate-500 hover:text-slate-700">Cancel</button>
      </div>
    </div>
  );
}
