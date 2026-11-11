"use client";

import { useEffect, useMemo, useState } from "react";

import { WorkspaceError } from "@/components/WorkspaceError";
import { fmtMoney } from "@/lib/fmt";
import { workspaceGet, workspacePut } from "@/lib/workspaceApi";

type Line = { cat_index: number; item_index: number; name: string; suggested: string; saved: string | null; scope: string };
type Review = { division: string; labels: string[]; lines: Line[]; scopes: { label: string; amount: number }[] };

/**
 * "Client: by division" PDF: priced per CSI division with Div 09 broken out
 * by scope (456 W Sample PM, 2026-10-27). The keyword grouping can misfile a
 * line, so the scope of each Div 09 line is shown here to check and correct
 * before the PDF opens. Choices are saved on the estimate; prices don't change.
 */
export function DivisionPdfMenuItem({ projectId, vid, withLines = false }: { projectId: string; vid: string; withLines?: boolean }) {
  const api = `/projects/${projectId}/workbench/estimate-versions/${vid}/subscopes`;
  // withLines: the same layout with each division's work and the assumptions
  // listed, no signature block (Amir, 2026-11-06).
  const pdf = `/api/workbench/estimate-selection-pdf/${projectId}/${vid}?mode=${withLines ? "divisions_lines" : "divisions"}`;
  const [open, setOpen] = useState(false);
  const [review, setReview] = useState<Review | null>(null);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);

  const key = (l: Line) => `${l.cat_index}:${l.item_index}`;
  const load = (r: Review) => {
    setReview(r);
    setPicked(Object.fromEntries(r.lines.map((l) => [key(l), l.scope])));
  };
  useEffect(() => {
    if (!open || review) return;
    workspaceGet<Review>(api).then(load).catch(setError);
  }, [open, review, api]);

  const changed = useMemo(
    () => (review ? review.lines.filter((l) => picked[key(l)] !== l.scope) : []),
    [review, picked],
  );

  const saveThenOpen = async () => {
    setError(null);
    // Open the tab inside the click: a tab opened after the save's await is
    // treated as a popup and blocked. Point it at the PDF once saved.
    const tab = window.open("about:blank", "_blank");
    if (tab) tab.opener = null;
    if (changed.length) {
      setSaving(true);
      try {
        load(await workspacePut<Review>(api, {
          division: "09",
          choices: changed.map((l) => ({ cat_index: l.cat_index, item_index: l.item_index, scope: picked[key(l)] })),
        }));
      } catch (e) {
        tab?.close();
        setError(e);
        setSaving(false);
        return;
      }
      setSaving(false);
    }
    const url = `${pdf}&_cb=${Date.now()}`;
    if (tab) tab.location.href = url;
    else window.location.href = url;
  };

  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-slate-100">
        {withLines ? "Client: by division, with line items…" : "Client: by division (Div 09 by scope)…"}
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/30 p-4" role="dialog" aria-modal="true">
          <div className="mt-8 w-full max-w-3xl rounded-md border border-slate-300 bg-white p-4 text-left shadow-lg">
            <div className="flex items-center justify-between">
              <div className="text-sm font-semibold">Div 09 Finishes: check each line&apos;s scope</div>
              <button type="button" onClick={() => setOpen(false)} className="text-sm text-slate-500" aria-label="Close">✕</button>
            </div>
            <p className="mt-1 text-[13px] text-slate-600">
              The PDF prices every division, and splits Div 09 into these scopes. Lines were grouped by their wording;
              change any that are wrong. Your choices are saved on this estimate. Prices do not change.
            </p>
            {error != null && <WorkspaceError error={error} className="mt-2" />}
            {!review && error == null && <div className="mt-3 text-[13px] text-slate-500">Loading…</div>}
            {review && review.lines.length === 0 && (
              <div className="mt-3 text-[13px] text-slate-600">This estimate has no Div 09 lines; the PDF shows divisions only.</div>
            )}
            {review && review.lines.length > 0 && (
              <>
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-slate-600">
                  {review.scopes.map((s) => (
                    <span key={s.label}>
                      {s.label}: <b className="tabular-nums">{fmtMoney(s.amount)}</b>
                    </span>
                  ))}
                  {changed.length > 0 && <span className="text-amber-700">(amounts update when saved)</span>}
                </div>
                <div className="mt-2 max-h-[55vh] overflow-y-auto rounded border border-slate-200">
                  <table className="w-full text-[13px]">
                    <tbody>
                      {review.lines.map((l) => (
                        <tr key={key(l)} className="border-b border-slate-100 last:border-0">
                          <td className="p-2 align-top">{l.name}</td>
                          <td className="w-64 p-2 align-top">
                            <select
                              value={picked[key(l)]}
                              onChange={(e) => setPicked((p) => ({ ...p, [key(l)]: e.target.value }))}
                              className={`w-full rounded border px-1 py-0.5 text-[12px] ${
                                picked[key(l)] !== l.suggested ? "border-amber-400 bg-amber-50" : "border-slate-300"
                              }`}
                            >
                              {review.labels.map((lb) => (
                                <option key={lb} value={lb}>
                                  {lb}
                                  {lb === l.suggested ? " (suggested)" : ""}
                                </option>
                              ))}
                            </select>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            <div className="mt-3 flex justify-end gap-2">
              <button type="button" onClick={() => setOpen(false)} className="rounded border border-slate-300 px-3 py-1.5 text-sm">
                Close
              </button>
              <button
                type="button"
                disabled={!review || saving}
                onClick={saveThenOpen}
                className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300"
              >
                {saving ? "Saving…" : changed.length ? `Save ${changed.length} change${changed.length === 1 ? "" : "s"} and open PDF` : "Open PDF"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
