"use client";

import { useEffect, useState } from "react";

import { usePortal } from "../portalCtx";
import { getMilestones, type MilestoneItem } from "@/lib/portal";

function fmtDate(iso: string): string {
  return new Date(iso + "T00:00:00").toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

export default function Progress() {
  const { project, loading } = usePortal();
  const [items, setItems] = useState<MilestoneItem[] | null>(null);
  const [target, setTarget] = useState<string | null>(null);

  useEffect(() => {
    if (project)
      getMilestones(project.id)
        .then((d) => { setItems(d.items); setTarget(d.target_finish); })
        .catch(() => setItems([]));
  }, [project]);

  if (loading || !project) return <div className="h-40" />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold" style={{ color: "#1D3146" }}>Progress</h1>
        {target && (
          <p className="text-sm text-slate-500 mt-1">
            Target completion <b className="text-slate-700">{fmtDate(target)}</b>
          </p>
        )}
      </div>

      {items && items.length === 0 && (
        <p className="text-sm text-slate-400">The project timeline will appear here shortly.</p>
      )}

      <ol className="relative">
        {items?.map((m, i) => {
          const color = m.state === "done" ? "#0068A0" : m.state === "current" ? "#1D3146" : "#cfd6dd";
          return (
            <li key={i} className="relative flex gap-4 pb-6 last:pb-0">
              {/* connector line */}
              {i < (items?.length ?? 0) - 1 && (
                <span className="absolute left-[9px] top-5 bottom-0 w-px" style={{ background: "#e5e1db" }} />
              )}
              {/* node */}
              <span className="relative mt-1 flex h-[19px] w-[19px] shrink-0 items-center justify-center rounded-full"
                    style={{ background: m.state === "upcoming" ? "#fff" : color, border: `2px solid ${color}` }}>
                {m.state === "done" && <span className="text-white text-[10px] leading-none">✓</span>}
                {m.state === "current" && <span className="h-2 w-2 rounded-full bg-white" />}
              </span>
              <div className="flex-1">
                <div className={`font-medium ${m.state === "upcoming" ? "text-slate-400" : "text-slate-800"}`}>
                  {m.label}
                </div>
                <div className="text-xs text-slate-400">
                  {fmtDate(m.date)}
                  {m.state === "current" && <span className="ml-2" style={{ color: "#1D3146" }}>In progress</span>}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
