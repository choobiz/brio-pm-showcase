"use client";

import { useEffect, useState } from "react";

import { getThumbnailUrl } from "@/lib/documents";

/** A photo document's small server-made thumbnail (Site and the full daily report). */
export function PhotoThumb({ id, name, className = "h-16 w-full" }: { id: string; name: string; className?: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    getThumbnailUrl(id)
      .then((r) => live && setUrl(r.thumbnail_url))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [id]);
  return url ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt={name} title={name} className={`${className} rounded object-cover`} />
  ) : (
    <div className={`${className} rounded bg-slate-200`} title={name} />
  );
}
