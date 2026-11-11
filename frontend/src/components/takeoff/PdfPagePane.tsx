"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Document as PDFDocument, Page as PDFPage, pdfjs } from "react-pdf";

import { getDownloadUrl } from "@/lib/documents";

// pdf.js needs an external worker. Same source as the Measure on a PDF page.
if (typeof window !== "undefined" && !pdfjs.GlobalWorkerOptions.workerSrc) {
  pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;
}

/**
 * The PDF page a measurement was taken on, shown in the Takeoff page's drawing
 * pane. It used to be a "See it on the sheet" link in an empty box, which read
 * as the drawing failing to load (rollout 2026-10-20).
 *
 * The page only: the strokes that make up the figure are marked on the
 * measuring page, which the link opens.
 */
export function PdfPagePane({
  documentId,
  filename,
  page,
  measureHref,
}: {
  documentId: string;
  filename: string;
  page: number | null;
  measureHref: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [width, setWidth] = useState(600);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    setUrl(null);
    setErr(null);
    getDownloadUrl(documentId)
      .then((d) => live && setUrl(d.download_url))
      .catch((e) => live && setErr((e as Error).message));
    return () => {
      live = false;
    };
  }, [documentId]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.max(240, el.clientWidth - 16)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div className="min-w-0 lg:sticky lg:top-4 lg:self-start">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs font-medium text-slate-700">
          {filename}
          {page ? <span className="ml-1 font-normal text-slate-400">· page {page}</span> : null}
        </p>
        <Link href={measureHref} className="text-xs text-blue-700">
          See the measured lines →
        </Link>
      </div>
      <p className="mt-1 text-xs text-slate-500">Measured on this PDF page. The lines that make up the figure are marked on the measuring page.</p>
      <div ref={box} className="mt-2 max-h-[36rem] overflow-auto border border-slate-200 bg-white p-2">
        {err ? (
          <p className="p-4 text-sm text-red-600">{err}</p>
        ) : !url ? (
          <p className="p-4 text-sm text-slate-400">Opening the PDF…</p>
        ) : (
          <PDFDocument
            file={url}
            loading={<p className="p-4 text-sm text-slate-400">Opening the PDF…</p>}
            error={<p className="p-4 text-sm text-red-600">This PDF could not be shown.</p>}
          >
            <PDFPage pageNumber={page ?? 1} width={width} renderTextLayer={false} renderAnnotationLayer={false} />
          </PDFDocument>
        )}
      </div>
    </div>
  );
}
