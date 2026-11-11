"use client";

import { useEffect, useState } from "react";

import { usePortal } from "../portalCtx";
import { getSelections, decideSelection, type PortalSelection } from "@/lib/portal";

function money(n: number): string {
  const s = Math.abs(n).toLocaleString("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 0 });
  return n < 0 ? `−${s}` : s;
}
function fmtDate(s: string): string {
  // A date-only "2026-11-18" is midnight UTC, which is the 17th in Vancouver: read it as a calendar day.
  const day = /^\d{4}-\d{2}-\d{2}$/.test(s);
  return new Date(day ? `${s}T12:00:00Z` : s).toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric", ...(day ? { timeZone: "UTC" } : {}) });
}

export default function SelectionsPage() {
  const { project, loading } = usePortal();
  const [rows, setRows] = useState<PortalSelection[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  // Local per-selection draft: chosen option id + comment.
  const [draft, setDraft] = useState<Record<string, { option?: string; comment: string }>>({});

  const reload = (pid: string) => getSelections(pid).then(setRows).catch(() => setRows([]));
  useEffect(() => {
    if (project) reload(project.id);
  }, [project]);

  const setChoice = (sid: string, option: string) =>
    setDraft((d) => ({ ...d, [sid]: { ...(d[sid] ?? { comment: "" }), option } }));
  const setComment = (sid: string, comment: string) =>
    setDraft((d) => ({ ...d, [sid]: { ...(d[sid] ?? {}), comment } }));

  async function submitSelect(s: PortalSelection) {
    const chosen = draft[s.id]?.option;
    if (!chosen) return;
    setBusy(s.id);
    try {
      await decideSelection(project!.id, s.id, { option_id: chosen, comment: draft[s.id]?.comment || undefined });
      await reload(project!.id);
    } finally {
      setBusy(null);
    }
  }

  async function submitApprove(s: PortalSelection, decision: "approved" | "changes_requested") {
    if (decision === "changes_requested" && !confirm("Request changes on this item?")) return;
    setBusy(s.id);
    try {
      await decideSelection(project!.id, s.id, { decision, comment: draft[s.id]?.comment || undefined });
      await reload(project!.id);
    } finally {
      setBusy(null);
    }
  }

  if (loading || !project) return <div className="h-40" />;

  const pending = rows.filter((r) => r.status === "pending");
  const done = rows.filter((r) => r.status !== "pending");

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold" style={{ color: "#1D3146" }}>Selections</h1>

      {rows.length === 0 && (
        <p className="text-sm text-slate-400">
          No selections to make right now. We&rsquo;ll let you know when there&rsquo;s a choice for you.
        </p>
      )}

      {pending.length > 0 && (
        <p className="text-xs text-amber-700">
          {pending.length} item{pending.length > 1 ? "s" : ""} awaiting your decision.
        </p>
      )}

      {/* Pending — actionable */}
      <div className="space-y-4">
        {pending.map((s) => (
          <div key={s.id} className="rounded-2xl border bg-white p-5 shadow-sm" style={{ borderColor: "#ece8e2" }}>
            <div className="flex items-baseline justify-between gap-3">
              <div className="text-base font-semibold text-slate-800">{s.title}</div>
              {(s.decide_by ?? s.due_date) && <div className="text-xs text-slate-500 shrink-0">decide by {fmtDate((s.decide_by ?? s.due_date)!)}</div>}
            </div>
            {s.category && <div className="text-xs text-slate-400 mt-0.5">{s.category}</div>}
            {s.description && <p className="text-sm text-slate-600 mt-2">{s.description}</p>}

            {s.decision_type === "select" ? (
              <>
                <div className="mt-3 space-y-2">
                  {s.options.map((o) => {
                    const active = draft[s.id]?.option === o.id;
                    return (
                      <button
                        key={o.id}
                        onClick={() => setChoice(s.id, o.id)}
                        className="w-full text-left rounded-xl border px-4 py-3 transition"
                        style={{
                          borderColor: active ? "#0068A0" : "#ece8e2",
                          background: active ? "#eff7fc" : "#fff",
                        }}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <div className="text-sm font-medium text-slate-800">{o.label}</div>
                            {o.description && <div className="text-xs text-slate-500 mt-0.5">{o.description}</div>}
                          </div>
                          {o.price_delta != null && o.price_delta !== 0 && (
                            <div className="text-sm text-slate-600 shrink-0">
                              {o.price_delta > 0 ? "+" : ""}{money(o.price_delta)}
                            </div>
                          )}
                        </div>
                      </button>
                    );
                  })}
                </div>
                <textarea
                  value={draft[s.id]?.comment ?? ""}
                  onChange={(e) => setComment(s.id, e.target.value)}
                  rows={2}
                  placeholder="Add a note (optional)"
                  className="mt-3 w-full rounded-xl border px-3 py-2 text-sm"
                  style={{ borderColor: "#ece8e2" }}
                />
                <button
                  onClick={() => submitSelect(s)}
                  disabled={busy === s.id || !draft[s.id]?.option}
                  className="mt-3 w-full rounded-xl py-2.5 text-sm font-semibold text-white disabled:opacity-50"
                  style={{ background: "#0068A0" }}
                >
                  {busy === s.id ? "Submitting…" : "Submit selection"}
                </button>
              </>
            ) : (
              <>
                <textarea
                  value={draft[s.id]?.comment ?? ""}
                  onChange={(e) => setComment(s.id, e.target.value)}
                  rows={2}
                  placeholder="Add a note (optional)"
                  className="mt-3 w-full rounded-xl border px-3 py-2 text-sm"
                  style={{ borderColor: "#ece8e2" }}
                />
                <div className="mt-3 flex gap-2">
                  <button
                    onClick={() => submitApprove(s, "approved")}
                    disabled={busy === s.id}
                    className="flex-1 rounded-xl py-2.5 text-sm font-semibold text-white disabled:opacity-50"
                    style={{ background: "#0a7d4f" }}
                  >
                    {busy === s.id ? "…" : "Approve"}
                  </button>
                  <button
                    onClick={() => submitApprove(s, "changes_requested")}
                    disabled={busy === s.id}
                    className="rounded-xl border px-4 py-2.5 text-sm font-medium text-slate-600 disabled:opacity-50"
                    style={{ borderColor: "#ece8e2" }}
                  >
                    Request changes
                  </button>
                </div>
              </>
            )}
          </div>
        ))}
      </div>

      {/* Decided — read-only recap */}
      {done.length > 0 && (
        <div className="space-y-2">
          <div className="text-[11px] uppercase tracking-wider text-slate-400">Your decisions</div>
          {done.map((s) => {
            const label =
              s.decision_type === "approve"
                ? s.selected_option_id === "approved"
                  ? "Approved"
                  : "Changes requested"
                : s.selected_option?.label ?? "—";
            return (
              <div key={s.id} className="rounded-xl border bg-white px-4 py-3 shadow-sm" style={{ borderColor: "#ece8e2" }}>
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm font-medium text-slate-800">{s.title}</div>
                  <span
                    className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold"
                    style={
                      s.status === "confirmed"
                        ? { background: "#e7f6ee", color: "#0a7d4f" }
                        : { background: "#eef4fb", color: "#0068A0" }
                    }
                  >
                    {s.status === "confirmed" ? "Confirmed" : "Submitted"}
                  </span>
                </div>
                <div className="text-sm text-slate-600 mt-0.5">
                  {label}
                  {s.client_comment && <span className="text-slate-400"> — “{s.client_comment}”</span>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
