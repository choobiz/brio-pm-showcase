"use client";

import { useEffect, useState } from "react";
import { getPreviewUrl, type DocumentPreview as Preview } from "@/lib/documents";

/**
 * Preview pane for a document.
 *
 * Downloading a file to find out what it is means leaving the project, opening
 * Preview, and coming back — for every drawing. The point of this pane is to
 * make "what is this file?" a click instead of a round trip.
 *
 * PDFs and images render inline via a presigned URL with an `inline`
 * disposition. Anything the browser cannot display says so and offers the
 * download instead of showing an empty frame, which is worse than saying no.
 */
export function DocumentPreview({
  documentId,
  filename,
  position,
  total,
  onPrev,
  onNext,
  onClose,
  onDownload,
}: {
  documentId: string;
  filename: string;
  /** 1-based place in the list behind this pane, for "3 of 27". */
  position?: number | null;
  total?: number;
  onPrev?: () => void;
  onNext?: () => void;
  onClose: () => void;
  onDownload: () => void;
}) {
  const [state, setState] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setState(null);
    setError(null);
    getPreviewUrl(documentId)
      .then((p) => live && setState(p))
      .catch((e) => live && setError(e?.message ?? "Could not load preview"));
    return () => {
      live = false;
    };
  }, [documentId]);

  // Escape closes — a preview that traps you is worse than no preview. Arrows
  // walk the list underneath without closing, which is the whole point of a
  // Quick Look: you hold space once and then skim.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        onNext?.();
      } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        onPrev?.();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onNext, onPrev]);

  const isImage = state?.content_type?.startsWith("image/");

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-slate-900/70"
      onClick={onClose}
    >
      <div
        className="m-auto flex h-[92vh] w-[94vw] max-w-6xl flex-col border border-slate-300 bg-white"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-4 border-b border-slate-200 px-4 py-2">
          <span className="truncate text-sm font-medium text-slate-700" title={filename}>
            {filename}
            {position && total ? (
              <span className="ml-2 font-normal text-slate-400">
                {position} of {total}
              </span>
            ) : null}
          </span>
          <div className="flex shrink-0 items-center gap-2">
            {onPrev && onNext ? (
              <>
                <button
                  type="button"
                  onClick={onPrev}
                  title="Previous (↑)"
                  className="border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
                >
                  ↑
                </button>
                <button
                  type="button"
                  onClick={onNext}
                  title="Next (↓)"
                  className="border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
                >
                  ↓
                </button>
              </>
            ) : null}
            <button
              type="button"
              onClick={onDownload}
              className="border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
            >
              Download
            </button>
            <button
              type="button"
              onClick={onClose}
              className="border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
            >
              Close
            </button>
          </div>
        </header>

        <div className="flex flex-1 items-center justify-center overflow-auto bg-slate-100">
          {error && <p className="p-6 text-sm text-red-600">{error}</p>}
          {!error && !state && (
            <p className="p-6 text-sm text-slate-400">Loading preview…</p>
          )}
          {state && !state.previewable && (
            <div className="max-w-md p-8 text-center">
              <p className="text-sm text-slate-600">{state.reason}</p>
              <button
                type="button"
                onClick={onDownload}
                className="mt-3 border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-white"
              >
                Download {filename}
              </button>
            </div>
          )}
          {state?.previewable && state.preview_url && (
            isImage ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={state.preview_url}
                alt={filename}
                className="max-h-full max-w-full object-contain"
              />
            ) : (
              <iframe
                src={state.preview_url}
                title={filename}
                className="h-full w-full border-0 bg-white"
              />
            )
          )}
        </div>
      </div>
    </div>
  );
}
