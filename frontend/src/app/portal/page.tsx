"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { usePortal } from "./portalCtx";
import { getOverview, type Overview } from "@/lib/portal";

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso + "T00:00:00").toLocaleDateString([], {
    month: "long", day: "numeric", year: "numeric",
  });
}

export default function PortalHome() {
  const { project, loading, denied } = usePortal();
  const [ov, setOv] = useState<Overview | null>(null);

  useEffect(() => {
    if (project) getOverview(project.id).then(setOv).catch(() => setOv(null));
  }, [project]);

  if (denied)
    return <p className="text-sm text-slate-500 mt-10 text-center">This portal is for BRIO clients.</p>;
  if (loading) return <Skeleton />;
  if (!project)
    return <p className="text-sm text-slate-500 mt-10 text-center">No project to show yet.</p>;

  return (
    <div className="space-y-6">
      <div>
        <div className="text-[11px] uppercase tracking-[0.2em] text-slate-400">Your project</div>
        <h1 className="text-2xl font-semibold mt-1" style={{ color: "#1D3146" }}>
          {project.name}
        </h1>
        {project.address && <p className="text-sm text-slate-500 mt-0.5">{project.address}</p>}
      </div>

      {/* Hero */}
      <div className="overflow-hidden rounded-2xl border shadow-sm" style={{ borderColor: "#ece8e2" }}>
        {ov?.hero_photo ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={ov.hero_photo.url} alt={ov.hero_photo.caption}
               className="w-full object-cover" style={{ height: "220px" }} />
        ) : (
          <div className="w-full flex items-center justify-center text-slate-300 text-sm"
               style={{ height: "160px", background: "#f1efec" }}>
            Photos of your project will appear here
          </div>
        )}
        <div className="bg-white p-5 space-y-4">
          <Row label="Current stage" value={ov?.current_phase ?? "—"} big />
          {ov?.next_milestone && (
            <Row label="Next milestone" value={`${ov.next_milestone.label} · ${fmtDate(ov.next_milestone.date)}`} />
          )}
          <Row label="Target completion" value={fmtDate(ov?.target_finish ?? null)} />
          {ov?.last_update && (
            <p className="text-xs text-slate-400 pt-1">Last updated {fmtDate(ov.last_update)}</p>
          )}
        </div>
      </div>

      {/* Tiles */}
      <div className="grid grid-cols-3 gap-3">
        <Tile href="/portal/progress" icon="◈" label="Progress" />
        <Tile href="/portal/photos" icon="❏" label="Photos" />
        <Tile href="/portal/selections" icon="☑" label="Selections" />
        <Tile href="/portal/documents" icon="▤" label="Documents" />
        <Tile href="/portal/messages" icon="✉" label="Messages" />
        <Tile href="/portal/warranty" icon="◎" label="Warranty" />
      </div>
    </div>
  );
}

function Row({ label, value, big }: { label: string; value: string; big?: boolean }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-slate-400">{label}</div>
      <div className={big ? "text-lg font-semibold" : "text-sm font-medium text-slate-700"}
           style={big ? { color: "#0068A0" } : undefined}>
        {value}
      </div>
    </div>
  );
}

function Tile({ href, icon, label }: { href: string; icon: string; label: string }) {
  return (
    <Link href={href}
      className="flex flex-col items-center justify-center gap-1.5 rounded-2xl border bg-white py-5 text-slate-600 shadow-sm transition hover:shadow-md"
      style={{ borderColor: "#ece8e2" }}>
      <span className="text-2xl" style={{ color: "#1D3146" }}>{icon}</span>
      <span className="text-xs font-medium">{label}</span>
    </Link>
  );
}

function Skeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="h-8 w-2/3 rounded bg-slate-200" />
      <div className="h-64 rounded-2xl bg-slate-200" />
      <div className="grid grid-cols-3 gap-3">
        {[0, 1, 2].map((i) => <div key={i} className="h-24 rounded-2xl bg-slate-200" />)}
      </div>
    </div>
  );
}
