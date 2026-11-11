"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { searchDocuments, type SearchHit, type SearchResponse } from "@/lib/documentSearch";
import { listProjects, PROJECT_TYPE_LABELS, type ProjectRead } from "@/lib/projects";

// Widened deliberately. A CHECK constraint now restricts projects.type to the
// four known values, but a row written before that constraint existed would
// still read back as a plain string — and showing it verbatim is better than
// showing "undefined".
const TYPE_LABEL: Record<string, string> = PROJECT_TYPE_LABELS;

/**
 * ⚠️ UNMOUNTED since UI round 1 phase 8 (2026-10-13), when /workspace was
 * retired. Kept as the starting point for the phase 9 Ask panel; its /workspace
 * links now redirect to the project pages.
 *
 * ⌘K — one way in, for projects and documents.
 *
 * A COMMAND PALETTE RATHER THAN A CHAT INPUT, and the distinction is the point.
 * A persistent text box invites conversation and then has to answer anything; a
 * palette invites a command and can be honest about the small number it knows.
 * It also has no blank-page problem: it opens with what you last touched and a
 * list of what it can do, so nobody has to guess what to type.
 *
 * Projects are filtered locally from one fetch — there are tens of them, and a
 * round trip per keystroke to filter a list you already have is latency for
 * nothing. Documents are searched server-side because there are thousands and
 * the ranking lives there.
 *
 * ⚠️ EVERY RESULT STAYS INSIDE /workspace. This component is mounted in the
 * workspace layout and nowhere else, but it used to route hits to
 * `/projects/<id>/overview` and `/projects/<id>/documents` — the LIVE app. So
 * the preview's own palette ejected you out of the preview on every single
 * result, which made ⌘K useless for the thing it exists to do here. If this is
 * ever mounted in the office app too, these hrefs need to become
 * pathname-aware rather than being flipped back.
 */

const DEBOUNCE_MS = 180;
const RECENT_KEY = "brio.cmdk.recent";
const MAX_RECENT = 5;

type Row =
  | { kind: "project"; id: string; label: string; sub: string; href: string }
  | { kind: "document"; id: string; label: string; sub: string; href: string; note: string | null };

function readRecent(): string[] {
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    return raw ? (JSON.parse(raw) as string[]).slice(0, MAX_RECENT) : [];
  } catch {
    return [];
  }
}

function pushRecent(id: string) {
  try {
    const next = [id, ...readRecent().filter((x) => x !== id)].slice(0, MAX_RECENT);
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* private mode — recents are a convenience, not state */
  }
}

export function CommandBar() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [projects, setProjects] = useState<ProjectRead[] | null>(null);
  const [docs, setDocs] = useState<SearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // ⌘K / Ctrl+K anywhere. Escape closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!open) return;
    setCursor(0);
    inputRef.current?.focus();
    if (projects === null) listProjects().then(setProjects).catch(() => setProjects([]));
  }, [open, projects]);

  // Debounced document search. Two characters minimum: one letter matches
  // thousands of filenames and tells you nothing.
  useEffect(() => {
    if (!open || q.trim().length < 2) {
      setDocs(null);
      return;
    }
    setSearching(true);
    const t = setTimeout(() => {
      searchDocuments(q, { limit: 8 })
        .then(setDocs)
        .catch(() => setDocs(null))
        .finally(() => setSearching(false));
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [q, open]);

  const rows = useMemo<Row[]>(() => {
    const term = q.trim().toLowerCase();
    const all = projects ?? [];
    const recent = typeof window !== "undefined" ? readRecent() : [];

    const matched = term
      ? all.filter((p) => p.name.toLowerCase().includes(term))
      : // Nothing typed: show what you last opened, then the rest.
        [...all].sort((a, b) => {
          const ia = recent.indexOf(a.id);
          const ib = recent.indexOf(b.id);
          if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
          return a.name.localeCompare(b.name);
        });

    const projectRows: Row[] = matched.slice(0, 6).map((p) => ({
      kind: "project",
      id: p.id,
      label: p.name,
      sub: p.type
        ? (TYPE_LABEL[p.type] ?? p.type)
        : "no type set — estimates would use a generic breakdown",
      href: `/workspace/projects/${p.id}`,
    }));

    const docRows: Row[] = (docs?.results ?? []).map((d: SearchHit) => ({
      kind: "document",
      id: d.id,
      label: d.filename,
      sub: `${d.project_name} · ${d.matched_on}`,
      // The workspace has no per-project documents route; documents live on the
      // project page, which is where this lands.
      href: `/workspace/projects/${d.project_id}`,
      note: d.snippet,
    }));

    return [...projectRows, ...docRows];
  }, [projects, q, docs]);

  const go = useCallback(
    (row: Row) => {
      if (row.kind === "project") pushRecent(row.id);
      setOpen(false);
      setQ("");
      router.push(row.href);
    },
    [router],
  );

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (e.key === "Enter" && rows[cursor]) {
      e.preventDefault();
      go(rows[cursor]);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-4 right-4 z-40 rounded-full border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600 shadow-sm hover:bg-slate-50"
        title="Jump to a project or find a document (⌘K)"
      >
        ⌘K
      </button>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/30 p-4 pt-[12vh]"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-xl overflow-hidden rounded-lg border border-slate-300 bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setCursor(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Jump to a project, or find a document by name…"
          className="w-full border-b border-slate-200 px-4 py-3 text-[15px] outline-none"
        />

        <div className="max-h-[52vh] overflow-y-auto">
          {rows.length === 0 && (
            <p className="px-4 py-6 text-center text-sm text-slate-500">
              {q.trim().length === 1
                ? "Keep typing — one letter matches too much to be useful."
                : searching
                  ? "Searching…"
                  : "Nothing matched."}
            </p>
          )}

          {rows.map((r, i) => (
            <button
              key={`${r.kind}:${r.id}`}
              onMouseEnter={() => setCursor(i)}
              onClick={() => go(r)}
              className={`flex w-full flex-col items-start gap-0.5 border-b border-slate-100 px-4 py-2.5 text-left ${
                i === cursor ? "bg-slate-100" : "bg-white hover:bg-slate-50"
              }`}
            >
              <span className="flex w-full items-baseline gap-2">
                <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-600">
                  {r.kind === "project" ? "project" : "document"}
                </span>
                <span className="truncate text-sm font-medium">{r.label}</span>
              </span>
              <span className="truncate pl-1 text-xs text-slate-500">{r.sub}</span>
              {r.kind === "document" && r.note && (
                <span className="pl-1 text-xs italic text-slate-400">…{r.note}</span>
              )}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 bg-slate-50 px-4 py-2 text-[11px] text-slate-500">
          <span>↑↓ move · ↵ open · esc close</span>
          {/* Said plainly, because documents have no revision chain yet and the
              palette must not imply one. */}
          {docs && <span className="truncate">{docs.ordering_note}</span>}
        </div>
      </div>
    </div>
  );
}
