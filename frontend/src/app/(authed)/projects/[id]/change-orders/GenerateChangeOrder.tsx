"use client";

import { useState } from "react";
import { createAIJob, getAIJob } from "@/lib/aiJobs";
import { listDocuments, type DocumentRead } from "@/lib/documents";
import { Card } from "@/components/ui/card";

/**
 * Price extra work from the project's own documents.
 *
 * Estimates have had a generation pipeline since the beginning; change orders
 * had none, so extra work was priced by typing a number into a box. The
 * temptation was to revise the estimate instead, which supersedes the version
 * the signed contract and the issued invoices were built from and throws away
 * the approval trail that makes a change defensible.
 *
 * The instruction field is not optional padding. Documents alone cannot tell
 * the model which work is additional and which was always in the contract, and
 * pricing contracted work as an extra is the way this feature could actually
 * cost someone money. The prompt is written around that, and so is this form.
 *
 * Bullets are the expected input, not prose. The draft expands them into line
 * items, assumptions and exclusions — CO-1 came back with 7 items, 10
 * assumptions and 9 exclusions from a sketch and two sentences. What it will
 * NOT do is invent an allowance or a provisional sum for something nobody has
 * measured yet, so a scope with real unknowns still wants a human on the
 * numbers afterwards.
 */
export default function GenerateChangeOrder({
  projectId,
  onGenerated,
}: {
  projectId: string;
  onGenerated: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [docs, setDocs] = useState<DocumentRead[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function expand() {
    setOpen(true);
    if (docs === null) {
      try {
        setDocs(await listDocuments(projectId));
      } catch (e) {
        setErr(String(e));
        setDocs([]);
      }
    }
  }

  function toggle(docId: string) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  }

  async function run() {
    if (!instruction.trim()) {
      setErr("Describe the extra work. Without it the documents cannot say what is additional.");
      return;
    }
    setBusy(true);
    setErr(null);
    setStatus("Queued…");
    try {
      const job = await createAIJob({
        project_id: projectId,
        kind: "change_order_generation",
        input: {
          instruction: instruction.trim(),
          document_ids: [...picked],
        },
      });

      // Poll rather than stream: this runs once, takes tens of seconds, and an
      // EventSource here would be more moving parts than the job is worth.
      const deadline = Date.now() + 5 * 60 * 1000;
      for (;;) {
        await new Promise((r) => setTimeout(r, 2500));
        const j = await getAIJob(job.id);
        setStatus(j.status === "running" ? "Pricing the work…" : `${j.status}…`);
        if (j.status === "done") {
          const n = (j.output as { change_order_number?: number } | null)?.change_order_number;
          setStatus(n ? `Created CO-${n} as a draft. Review it below.` : "Draft created.");
          setInstruction("");
          setPicked(new Set());
          onGenerated();
          break;
        }
        if (j.status === "error") {
          setErr(j.error || "generation failed");
          setStatus(null);
          break;
        }
        if (Date.now() > deadline) {
          setErr("Timed out waiting for the job. It may still finish; reload to check.");
          setStatus(null);
          break;
        }
      }
    } catch (e) {
      setErr(String(e));
      setStatus(null);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={expand}
        className="text-sm font-medium text-[var(--color-brio-accent)] hover:underline"
      >
        Generate from documents
      </button>
    );
  }

  return (
    <Card className="space-y-3 p-4">
      <div className="text-sm font-semibold uppercase tracking-wide text-slate-500">
        Generate a change order from documents
      </div>

      <label className="block text-xs text-slate-500">
        What is the extra work? Be specific about what is additional to the contract.
        <span className="ml-1 text-slate-400">
          Bullet points are enough — the draft writes the assumptions and exclusions.
        </span>
        {/* The placeholder used to be one prose sentence, which read as an
            instruction to write a paragraph. It is bullets that people actually
            have to hand, the prompt handles them, and a shape you can copy is
            worth more than a sentence you have to imitate. */}
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          disabled={busy}
          rows={6}
          placeholder={`e.g.
- Replace soffit on back (38 lf) and west (42 lf) elevations, pine painted black, 4 ft wide
- Remove existing aluminum soffit, allow for hidden fascia rot
- New flashing with a larger lip, 21 lf front and 8 lf west
- Driveway ponds at the lot entrance, needs re-sloping

Say what each item is, and roughly how much of it there is. Anything you do not
know yet, write that too — an unknown priced as an allowance beats a guess.`}
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
        />
      </label>

      <div className="text-xs text-slate-500">
        Attach documents the pricing should read (optional)
        <div className="mt-1 max-h-44 space-y-1 overflow-y-auto rounded border border-slate-200 p-2">
          {docs === null && <div className="text-slate-400">Loading documents…</div>}
          {docs?.length === 0 && <div className="text-slate-400">No documents on this project.</div>}
          {docs?.map((d) => (
            <label key={d.id} className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={picked.has(d.id)}
                onChange={() => toggle(d.id)}
                disabled={busy}
              />
              <span className="truncate">{d.filename}</span>
            </label>
          ))}
        </div>
      </div>

      {err && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {err}
        </div>
      )}
      {status && !err && <div className="text-sm text-slate-600">{status}</div>}

      <div className="flex items-center gap-3">
        <button
          onClick={run}
          disabled={busy}
          className="rounded bg-[var(--color-brio-accent)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
        >
          {busy ? "Working…" : "Generate draft"}
        </button>
        <button
          onClick={() => setOpen(false)}
          disabled={busy}
          className="text-sm text-slate-500 hover:text-slate-700"
        >
          Cancel
        </button>
        <span className="text-xs text-slate-400">
          Creates a draft. Nothing is sent or approved.
        </span>
      </div>
    </Card>
  );
}
