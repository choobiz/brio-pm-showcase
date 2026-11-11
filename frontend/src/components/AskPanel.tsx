"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { askAssistant, getAskEstimate, type AskSource, type AskTurn } from "@/lib/assistant";
import { getProject, listProjects, type ProjectRead } from "@/lib/projects";
import { errorMessage } from "@/lib/workspaceApi";

/**
 * Ask — one box that does two things (UI round 1, phase 9, approved
 * 2026-10-13):
 *
 *   1. GO SOMEWHERE. Typing a project or section name lists matches at once,
 *      with no AI involved.
 *   2. ANSWER FROM LIVE DATA. Enter sends the question to /assistant/ask,
 *      which reads the same records the pages do. Every answer shows the
 *      records it came from and when each was last updated.
 *
 * Doing things (prepare an invoice, send a change order) is a later release,
 * and will always propose before acting.
 *
 * Opened by the Ask button in the sidebar (a `brio:ask` event) or ⌘K / Ctrl+K.
 * On a project page, "this project" means that project.
 */

export const ASK_EVENT = "brio:ask";

const SECTIONS: { label: string; path: string }[] = [
  { label: "Overview", path: "/overview" },
  { label: "Documents", path: "/documents" },
  { label: "Scope", path: "/scope" },
  { label: "Takeoff", path: "/takeoff" },
  { label: "Estimate", path: "/estimate" },
  { label: "Schedule", path: "/workbench/schedule" },
  { label: "Site", path: "/site" },
  { label: "Money", path: "/money" },
  { label: "Messages", path: "/messages" },
];

/** "Prepare a forming lumber order…" is a document to draft, not a question. */
const PREPARE = /\b(prepare|create|make|draft|write|generate)\b.*\b(order|take-?off|request|sheet|form|list|rfq|quote request)\b/i;

const STARTERS_PROJECT = [
  "What's the current status of this project?",
  "What work is scheduled for this week?",
  "What's blocking the next phase of work?",
  "Which change orders are waiting for approval?",
  "What invoices or payments need attention?",
];
const STARTERS_ALL = [
  "What needs my attention today?",
  "Which change orders are waiting for approval?",
  "What invoices or payments need attention?",
];

type Turn = AskTurn & { sources?: AskSource[] };

function since(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function AskPanel() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectRead[] | null>(null);
  const [here, setHere] = useState<ProjectRead | null>(null);
  /** Up-to cost of one question, shown before asking; asking approves it. */
  const [estimate, setEstimate] = useState<number | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const projectId = pathname.match(/^\/projects\/([0-9a-f-]{36})/)?.[1] ?? null;

  useEffect(() => {
    const show = () => setOpen(true);
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener(ASK_EVENT, show);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener(ASK_EVENT, show);
      window.removeEventListener("keydown", key);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    if (projects === null) listProjects().then(setProjects).catch(() => setProjects([]));
    getAskEstimate().then((e) => setEstimate(e.usd)).catch(() => setEstimate(null));
  }, [open, projects]);

  useEffect(() => {
    let live = true;
    setHere(null);
    if (projectId) getProject(projectId).then((p) => live && setHere(p)).catch(() => {});
    return () => {
      live = false;
    };
  }, [projectId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [turns, busy]);

  // Go-to matches: instant, no AI.
  const goto = useMemo(() => {
    const raw = text.trim();
    if (projectId && PREPARE.test(raw))
      return [{ label: "Prepare this as a document", href: `/projects/${projectId}/prepare?q=${encodeURIComponent(raw)}` }];
    const q = raw.toLowerCase();
    if (q.length < 2 || q.includes("?") || q.split(/\s+/).length > 4) return [];
    const out: { label: string; href: string }[] = [];
    if (projectId) {
      for (const s of SECTIONS)
        if (s.label.toLowerCase().startsWith(q)) out.push({ label: s.label, href: `/projects/${projectId}${s.path}` });
    }
    for (const p of projects ?? []) {
      const hay = `${p.name} ${p.address ?? ""}`.toLowerCase();
      if (q.split(/\s+/).every((w) => hay.includes(w)))
        out.push({ label: p.name, href: `/projects/${p.id}/overview` });
      if (out.length >= 6) break;
    }
    return out;
  }, [text, projects, projectId]);

  const go = (href: string) => {
    setOpen(false);
    setText("");
    router.push(href);
  };

  const send = useCallback(
    async (question: string) => {
      const q = question.trim();
      if (!q || busy || estimate === null) return;
      if (projectId && PREPARE.test(q)) {
        go(`/projects/${projectId}/prepare?q=${encodeURIComponent(q)}`);
        return;
      }
      setErr(null);
      setBusy(true);
      setText("");
      const history: AskTurn[] = turns.map(({ role, content }) => ({ role, content }));
      setTurns((t) => [...t, { role: "user", content: q }]);
      try {
        const r = await askAssistant(q, projectId, history, estimate);
        setTurns((t) => [...t, { role: "assistant", content: r.answer, sources: r.sources }]);
      } catch (e) {
        setErr(errorMessage(e));
        setTurns((t) => t.slice(0, -1));
        setText(q);
      } finally {
        setBusy(false);
      }
    },
    [busy, turns, projectId, estimate],
  );

  if (!open) return null;

  const starters = projectId ? STARTERS_PROJECT : STARTERS_ALL;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-label="Ask">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-slate-900/20" onClick={() => setOpen(false)} />
      <section className="relative flex h-full w-full max-w-[440px] flex-col border-l border-slate-200 bg-white shadow-xl">
        <header className="flex items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
          <div className="min-w-0">
            <div className="font-semibold text-slate-900">Ask</div>
            <div className="truncate text-xs text-slate-600">
              {projectId ? `About ${here?.name ?? "this project"}` : "Across all your projects"} · read-only
            </div>
          </div>
          <div className="flex items-center gap-1">
            {turns.length > 0 && (
              <button type="button" onClick={() => { setTurns([]); setErr(null); }}
                className="rounded px-2 py-1 text-xs text-slate-600 hover:bg-slate-100">
                New
              </button>
            )}
            <button type="button" onClick={() => setOpen(false)} aria-label="Close"
              className="rounded px-2 py-1 text-lg leading-none text-slate-500 hover:bg-slate-100">
              ×
            </button>
          </div>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4 text-sm">
          {turns.length === 0 && !busy && (
            <div className="space-y-2">
              <p className="m-0 text-slate-600">Ask about status, this week, blockers, change orders or invoices. Answers come from the app&rsquo;s records, with links.</p>
              {projectId && (
                <button type="button" onClick={() => go(`/projects/${projectId}/prepare`)}
                  className="block w-full rounded-md border border-slate-900 px-3 py-2 text-left font-medium text-slate-900 hover:bg-slate-50">
                  Prepare a document: an order, takeoff, rental request or crew sheet →
                </button>
              )}
              {starters.map((s) => (
                <button key={s} type="button" onClick={() => send(s)} disabled={estimate === null}
                  className="flex w-full items-center justify-between gap-2 rounded-md border border-slate-200 px-3 py-2 text-left text-slate-800 hover:bg-slate-50 disabled:opacity-50">
                  <span>{s}</span>
                  {estimate !== null && <span className="shrink-0 text-xs text-slate-500">up to ${estimate.toFixed(2)}</span>}
                </button>
              ))}
            </div>
          )}
          {turns.map((t, i) =>
            t.role === "user" ? (
              <div key={i} className="ml-8 rounded-md bg-slate-100 px-3 py-2 text-slate-900">{t.content}</div>
            ) : (
              <div key={i} className="space-y-2">
                <div className="prose prose-sm max-w-none prose-a:text-blue-700">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({ href, children }) =>
                        href?.startsWith("/") ? (
                          <Link href={href} onClick={() => setOpen(false)}>{children}</Link>
                        ) : (
                          <span>{children}</span>
                        ),
                    }}
                  >
                    {t.content}
                  </ReactMarkdown>
                </div>
                {t.sources && t.sources.length > 0 && (
                  <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
                    <div className="mb-1 font-medium text-slate-700">From</div>
                    <ul className="m-0 list-none space-y-0.5 p-0">
                      {t.sources.map((s) => (
                        <li key={s.href} className="flex justify-between gap-2">
                          <Link href={s.href} onClick={() => setOpen(false)} className="truncate text-blue-700">{s.label}</Link>
                          {since(s.updated_at) && <span className="shrink-0 text-slate-500">updated {since(s.updated_at)}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ),
          )}
          {busy && <p className="m-0 text-slate-500">Looking it up…</p>}
          {err && <p className="m-0 text-red-700">{err}</p>}
          <div ref={bottom} />
        </div>

        <div className="border-t border-slate-200 p-3">
          {goto.length > 0 && (
            <ul className="m-0 mb-2 list-none p-0 text-sm">
              {goto.map((g) => (
                <li key={g.href}>
                  <button type="button" onClick={() => go(g.href)}
                    className="flex w-full items-center justify-between rounded px-2 py-1.5 text-left hover:bg-slate-100">
                    <span className="truncate">{g.label}</span>
                    <span className="text-xs text-slate-500">Go to</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <textarea
            ref={input}
            rows={2}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(text);
              }
            }}
            placeholder={projectId ? "Ask about this project, or type a page to go to" : "Ask, or type a project to go to"}
            className="w-full resize-none rounded-md border border-slate-300 px-3 py-2 text-sm"
          />
          <div className="mt-1 flex items-center justify-between text-xs text-slate-500">
            <span>
              {estimate === null ? "Estimating the cost…" : `Each question costs up to $${estimate.toFixed(2)}; asking approves it`}
            </span>
            <button type="button" disabled={busy || !text.trim() || estimate === null} onClick={() => send(text)}
              className="rounded bg-slate-900 px-3 py-1 text-white disabled:opacity-40">
              {estimate === null ? "Ask" : `Ask · up to $${estimate.toFixed(2)}`}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
