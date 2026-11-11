import Link from "next/link";
import { listNarratives, fmtDate, type NarrativeSummary } from "@/lib/workbench";

function Row({ pid, n }: { pid: string; n: NarrativeSummary }) {
  return (
    <li className="border border-slate-200 rounded p-3 hover:bg-slate-50">
      <Link href={`/projects/${pid}/workbench/narratives/${n.id}`} className="block">
        <div className="flex items-baseline justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium text-blue-700">{n.title}</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              v{n.version_number} · parent: {n.parent_table} ·{" "}
              {Math.round(n.content_size / 1024)} KB
            </p>
          </div>
          <span className="text-xs text-slate-500 shrink-0">{fmtDate(n.created_at)}</span>
        </div>
      </Link>
    </li>
  );
}

export default async function NarrativesIndex({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  // ⚠️ An empty list and a failed fetch are different facts and must not render
  // the same way. This page used to do `.catch(() => [])`, so when the API was
  // restarting during a deploy it showed the empty state on a project holding
  // five notes, with nothing to tell a reader which it was.
  //
  // The message deliberately carries NO error text. On 2 Oct a sibling surface
  // rendered nginx's raw 502 page into the UI; the technical detail belongs in
  // the server log, and the reader needs a sentence and a next step.
  let narratives: NarrativeSummary[] = [];
  let loadFailed = false;
  try {
    narratives = await listNarratives(id);
  } catch (e) {
    loadFailed = true;
    console.error("narratives list failed for project", id, e);
  }

  // Every revision is a new row, so the raw list carries each note's whole
  // history. Showing them side by side with nothing marking the live one is how
  // a reader ends up working from a superseded version.
  const current = narratives.filter((n) => !n.is_superseded);
  const earlier = narratives.filter((n) => n.is_superseded);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Notes</h1>
        <p className="text-sm text-slate-600 mt-1">
          Briefs, registers and takeoffs for this project. Each one can be printed to a BRIO PDF and sent to a supplier.
        </p>
      </header>

      {loadFailed ? (
        <div className="border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <strong>The notes could not be loaded.</strong> This is a loading failure, not an
          empty project, so do not read it as the notes being gone. Reload in a few seconds.
          If it keeps happening the API may be restarting after a deploy.
        </div>
      ) : current.length === 0 ? (
        <div className="text-sm text-slate-500">No notes yet.</div>
      ) : (
        <ul className="space-y-2">
          {current.map((n) => (
            <Row key={n.id} pid={id} n={n} />
          ))}
        </ul>
      )}

      {earlier.length > 0 && (
        <details className="group">
          <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-700">
            {earlier.length} earlier version{earlier.length === 1 ? "" : "s"} — superseded
          </summary>
          <ul className="space-y-2 mt-2 opacity-60">
            {earlier.map((n) => (
              <Row key={n.id} pid={id} n={n} />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
