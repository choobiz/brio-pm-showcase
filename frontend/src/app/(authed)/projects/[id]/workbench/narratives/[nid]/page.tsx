import Link from "next/link";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

import { getNarrative, fmtDate } from "@/lib/workbench";

export default async function NarrativeDetail({
  params,
}: {
  params: Promise<{ id: string; nid: string }>;
}) {
  const { id, nid } = await params;
  const n = await getNarrative(id, nid);

  return (
    <div className="space-y-4">
      <header>
        <Link
          href={`/projects/${id}/workbench/narratives`}
          className="text-sm text-blue-600 hover:underline"
        >
          ← All narratives
        </Link>
        <h1 className="text-xl font-semibold mt-1">{n.title}</h1>
        <div className="flex items-baseline justify-between gap-3 mt-1">
          <p className="text-xs text-slate-500">
            v{n.version_number} · {fmtDate(n.created_at)}
          </p>
          {/* Suppliers get no login, so the note has to become a file. */}
          <a
            href={`/api/backend/projects/${id}/workbench/narratives/${nid}/pdf`}
            target="_blank"
            rel="noreferrer"
            className="text-xs font-medium text-blue-600 hover:underline"
          >
            Print to PDF
          </a>
        </div>
      </header>

      {n.superseded_by && (
        <div className="border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <strong>This is an older version.</strong> v{n.superseded_by.version_number}{" "}
          replaced it —{" "}
          <Link
            href={`/projects/${id}/workbench/narratives/${n.superseded_by.id}`}
            className="font-medium underline"
          >
            read the current version
          </Link>
          .
        </div>
      )}

      <article className="prose prose-slate prose-sm max-w-none rounded border border-slate-200 bg-white p-5 prose-headings:text-[var(--color-brio-accent)] prose-a:text-blue-600 prose-table:text-sm">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          urlTransform={(url) => {
            // Images are written as `![alt](doc:<uuid>)`; the backend hands back
            // a fresh presigned URL per read. `defaultUrlTransform` would drop
            // the whole thing — `doc:` is not in its safe-protocol list.
            const m = /^doc:([0-9a-fA-F-]{36})$/.exec(url);
            if (m) return n.images?.[m[1].toLowerCase()] ?? "";
            return defaultUrlTransform(url);
          }}
        >
          {n.content_md}
        </ReactMarkdown>
      </article>
    </div>
  );
}
