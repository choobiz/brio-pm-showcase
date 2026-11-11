"use client";

import { useEffect, useState } from "react";

import { usePortal } from "../portalCtx";
import { getPhotos, type Photo } from "@/lib/portal";

export default function Photos() {
  const { project, loading } = usePortal();
  const [photos, setPhotos] = useState<Photo[] | null>(null);
  const [active, setActive] = useState<Photo | null>(null);

  useEffect(() => {
    if (project) getPhotos(project.id).then(setPhotos).catch(() => setPhotos([]));
  }, [project]);

  if (loading || !project) return <div className="h-40" />;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold" style={{ color: "#1D3146" }}>Photos</h1>

      {photos && photos.length === 0 && (
        <p className="text-sm text-slate-400">Your team will share progress photos here as work moves along.</p>
      )}

      <div className="grid grid-cols-2 gap-3">
        {photos?.map((p, i) => (
          <button key={i} type="button" onClick={() => setActive(p)}
            className="overflow-hidden rounded-xl border shadow-sm transition hover:shadow-md"
            style={{ borderColor: "#ece8e2" }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={p.url} alt={p.caption} className="w-full object-cover" style={{ height: "130px" }} />
          </button>
        ))}
      </div>

      {/* Lightbox */}
      {active && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/80 p-4"
             onClick={() => setActive(null)}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={active.url} alt={active.caption} className="max-h-[85vh] max-w-full rounded-lg" />
        </div>
      )}
    </div>
  );
}
