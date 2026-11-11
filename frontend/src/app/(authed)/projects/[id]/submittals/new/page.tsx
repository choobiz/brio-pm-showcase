"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useMemo, useState } from "react";

import { listDocuments, uploadDocument, type DocumentRead } from "@/lib/documents";
import { listChecklists, startReview, suggest, type Checklist, type Cost } from "@/lib/submittals";

import { ChecklistEditor } from "./ChecklistEditor";

const RULES = [
  "Every value cites its sheet and the label it was read from.",
  "A pipe or sump is joined only by its label, never by the elevation next to it.",
  'A value it cannot find is "Not shown", never a guess.',
];

/**
 * Start a review (wireframe B, approved 2026-10-17): what came in, what to check
 * it against, then the cost and Run. Nothing runs until Run is pressed, and the
 * approved amount travels with the job.
 */
export default function NewSubmittalPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [docs, setDocs] = useState<DocumentRead[]>([]);
  const [lists, setLists] = useState<Checklist[]>([]);
  const [incoming, setIncoming] = useState<Set<string>>(new Set());
  const [governing, setGoverning] = useState<Set<string>>(new Set());
  const [touchedGov, setTouchedGov] = useState(false);
  const [suggested, setSuggested] = useState<Set<string>>(new Set());
  const [trade, setTrade] = useState("stormwater");
  const [name, setName] = useState("");
  const [supplier, setSupplier] = useState("");
  const [revision, setRevision] = useState("REV 0");
  const [instructions, setInstructions] = useState("");
  const [cost, setCost] = useState<Cost | null>(null);
  // The selection the shown cost was worked out for; Run waits until they agree.
  const [costFor, setCostFor] = useState<string | null>(null);
  const [filterIn, setFilterIn] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<Checklist | "new" | null>(null);

  useEffect(() => {
    listDocuments(id).then(setDocs).catch((e) => setErr(String(e)));
    listChecklists(id).then(setLists).catch((e) => setErr(String(e)));
  }, [id]);

  // Suggested governing files for the trade, and the up-to cost of what is ticked.
  // Keyed on the ticks as strings: until the PM touches them the effect sets
  // `governing` itself, and a Set dependency would re-run it forever.
  const inKey = Array.from(incoming).sort().join(",");
  const govKey = touchedGov ? Array.from(governing).sort().join(",") : "";
  useEffect(() => {
    const t = setTimeout(() => {
      suggest(id, {
        trade,
        submittal_doc_ids: inKey ? inKey.split(",") : [],
        governing_doc_ids: touchedGov ? (govKey ? govKey.split(",") : []) : undefined,
      })
        .then((r) => {
          setSuggested(new Set(r.suggested));
          if (!touchedGov) setGoverning(new Set(r.suggested));
          setCost(r.cost);
          setCostFor(`${trade}|${inKey}|${touchedGov ? govKey : r.suggested.slice().sort().join(",")}`);
        })
        .catch(() => setCost(null));
    }, 250);
    return () => clearTimeout(t);
  }, [id, trade, inKey, govKey, touchedGov]);

  const checklist = lists.find((c) => c.key === trade);
  const toggle = (set: Set<string>, v: string) => {
    const n = new Set(set);
    if (n.has(v)) n.delete(v);
    else n.add(v);
    return n;
  };
  const byName = (a: DocumentRead, b: DocumentRead) => a.filename.localeCompare(b.filename);
  const incomingDocs = useMemo(
    () =>
      docs
        .filter((d) => incoming.has(d.id) || !filterIn || d.filename.toLowerCase().includes(filterIn.toLowerCase()))
        .sort((a, b) => Number(incoming.has(b.id)) - Number(incoming.has(a.id)) || byName(a, b)),
    [docs, incoming, filterIn],
  );
  const govDocs = useMemo(
    () =>
      docs
        .filter((d) => !incoming.has(d.id))
        .sort((a, b) => Number(suggested.has(b.id)) - Number(suggested.has(a.id)) || byName(a, b)),
    [docs, incoming, suggested],
  );

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setErr(null);
    try {
      for (const f of Array.from(files)) {
        const d = await uploadDocument(id, f, /quote|invoice|price|proposal/i.test(f.name) ? "quote" : "drawing");
        setDocs((x) => [d, ...x]);
        setIncoming((s) => new Set(s).add(d.id));
        if (!name) setName(f.name.replace(/\.[a-z0-9]+$/i, ""));
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  const selection = `${trade}|${inKey}|${Array.from(governing).sort().join(",")}`;
  const ready = name.trim() && incoming.size > 0 && governing.size > 0 && cost && costFor === selection;
  const run = async () => {
    if (!ready || !cost) return;
    setBusy(true);
    setErr(null);
    try {
      const s = await startReview(id, {
        name: name.trim(),
        trade,
        supplier: supplier.trim() || undefined,
        revision: revision.trim() || "REV 0",
        submittal_doc_ids: Array.from(incoming),
        governing_doc_ids: Array.from(governing),
        instructions: instructions.trim() || undefined,
        approve_usd: cost.usd,
      });
      router.push(`/projects/${id}/submittals/${s.id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const box = "flex flex-col gap-3 rounded-md border border-slate-200 bg-white p-4";
  const inp = "w-full rounded border border-slate-300 px-2 py-1.5 text-sm";

  return (
    <div className="space-y-4">
      <div>
        <Link href={`/projects/${id}/submittals`} className="text-sm text-blue-700">
          Back to Submittals
        </Link>
        <h1 className="m-0 text-2xl font-semibold">Review a submittal</h1>
        <p className="m-0 text-sm text-slate-600">Three steps on one page. Nothing runs until you press Run.</p>
      </div>

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-3">
        <section className={box}>
          <h2 className="m-0 text-base font-semibold">1 · What came in</h2>
          <label className="flex cursor-pointer flex-col items-center rounded-md border border-dashed border-slate-400 p-4 text-center text-sm text-slate-600">
            {uploading ? "Uploading…" : "Add the shop drawing, product data or quote"}
            <input type="file" multiple className="sr-only" onChange={(e) => upload(e.target.files)} />
          </label>
          <input className={inp} placeholder="…or find it in Documents" value={filterIn} onChange={(e) => setFilterIn(e.target.value)} />
          <div className="max-h-56 overflow-y-auto text-[13px]">
            {incomingDocs.slice(0, 60).map((d) => (
              <label key={d.id} className="flex items-start gap-2 py-0.5">
                <input
                  type="checkbox"
                  checked={incoming.has(d.id)}
                  onChange={() => {
                    setIncoming((s) => toggle(s, d.id));
                    // A file can't be both the submittal and a governing document.
                    setGoverning((g) => {
                      if (!g.has(d.id)) return g;
                      const n = new Set(g);
                      n.delete(d.id);
                      return n;
                    });
                  }}
                />
                <span>{d.filename}</span>
              </label>
            ))}
          </div>
          <label className="text-[13px]">
            Name
            <input className={inp} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. EcoBloc infiltration tank #2" />
          </label>
          <div className="flex gap-2">
            <label className="flex-1 text-[13px]">
              From
              <input className={inp} value={supplier} onChange={(e) => setSupplier(e.target.value)} placeholder="Supplier" />
            </label>
            <label className="w-24 text-[13px]">
              Revision
              <input className={inp} value={revision} onChange={(e) => setRevision(e.target.value)} />
            </label>
          </div>
          <p className="m-0 text-xs text-slate-500">
            A later revision of the same item starts from its review page, so it is checked against this one&apos;s findings.
          </p>
        </section>

        <section className={box}>
          <h2 className="m-0 text-base font-semibold">2 · What to check it against</h2>
          <label className="text-[13px]">
            Trade checklist
            <select
              className={inp}
              value={trade}
              onChange={(e) => (e.target.value === "__new" ? setEditing("new") : setTrade(e.target.value))}
            >
              {lists.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
              <option value="__new">+ New checklist…</option>
            </select>
          </label>
          {checklist && (
            <div className="rounded bg-slate-50 p-3 text-[13px]">
              <div className="font-semibold">It will compare</div>
              <div>{checklist.checks.map((c) => c.label).join(" · ")}</div>
              <button type="button" onClick={() => setEditing(checklist)} className="mt-1 text-xs text-blue-700 hover:underline">
                Edit this checklist{checklist.edited ? " (edited)" : ""}
              </button>
            </div>
          )}
          {editing && (
            <ChecklistEditor
              checklist={editing === "new" ? null : editing}
              onClose={() => setEditing(null)}
              onSaved={(k) => {
                setEditing(null);
                listChecklists(id)
                  .then((l) => {
                    setLists(l);
                    setTrade(k && l.some((c) => c.key === k) ? k : l[0]?.key ?? "stormwater");
                  })
                  .catch((e) => setErr(String(e)));
              }}
            />
          )}
          <div className="text-[13px] font-semibold">Governing documents</div>
          <div className="max-h-64 overflow-y-auto text-[13px]">
            {govDocs.map((d) => (
              <label key={d.id} className="flex items-start gap-2 py-0.5">
                <input
                  type="checkbox"
                  checked={governing.has(d.id)}
                  onChange={() => {
                    setTouchedGov(true);
                    setGoverning((s) => toggle(s, d.id));
                  }}
                />
                <span>
                  {d.filename}
                  {suggested.has(d.id) && <span className="text-slate-500"> · suggested</span>}
                </span>
              </label>
            ))}
          </div>
          <p className="m-0 text-xs text-slate-500">You choose the files; nothing is skipped because of its folder.</p>
        </section>

        <section className={box}>
          <h2 className="m-0 text-base font-semibold">3 · Run it</h2>
          <dl className="m-0 grid grid-cols-[1fr_auto] gap-y-1 text-[13px]">
            <dt className="text-slate-500">Files to read</dt>
            <dd className="m-0">
              {incoming.size} + {governing.size}
            </dd>
            <dt className="text-slate-500">Pages</dt>
            <dd className="m-0">{cost?.pages ?? "…"}</dd>
            <dt className="text-slate-500">Checks</dt>
            <dd className="m-0">{checklist?.checks.length ?? "…"}</dd>
            <dt className="text-slate-500">Up to</dt>
            <dd className="m-0 font-semibold">{cost ? `$${cost.usd.toFixed(2)}` : "…"}</dd>
          </dl>
          {cost && <p className="m-0 text-xs text-slate-500">{cost.how}</p>}
          <label className="text-[13px]">
            Anything to look at in particular (optional)
            <textarea rows={3} className={inp} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="e.g. we usually use 4 inch pipe" />
          </label>
          <button type="button" disabled={!ready || busy} onClick={run} className="rounded-md bg-slate-900 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50">
            {busy ? "Starting…" : cost ? `Run the review (up to $${cost.usd.toFixed(2)})` : "Run the review"}
          </button>
          {!ready && (
            <p className="m-0 text-xs text-slate-500">Pick what came in, at least one governing document, and give it a name.</p>
          )}
          <div className="border-t border-slate-100 pt-2 text-xs text-slate-600">
            <div className="font-semibold text-slate-900">Rules the reviewer follows</div>
            {RULES.map((r) => (
              <div key={r}>{r}</div>
            ))}
          </div>
          {err && <p className="m-0 text-sm text-red-700">{err}</p>}
        </section>
      </div>
    </div>
  );
}
