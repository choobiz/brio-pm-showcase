// No imports: scripts/check-scope-pack-links.mjs loads this file directly in Node.

export type ScopeForMenu = {
  id: string;
  version: number;
  created_at?: string;
  payload?: { reference?: string; summary?: string; title?: string; [k: string]: unknown } | null;
};

/** What the server says this estimate's PDF will use (GET .../pdf-scope). */
export type PdfScope = {
  scope: { id: string; version: number; reference?: string | null; summary?: string } | null;
  scope_count: number;
};

/** A short name for a scope: its reference or title, else the first words of
 * its summary, always with its version. */
export function scopeLabel(s: { version: number; reference?: string | null; summary?: string; payload?: ScopeForMenu["payload"] }): string {
  const p = s.payload ?? {};
  const ref = s.reference ?? (typeof p.reference === "string" ? p.reference : undefined) ?? (typeof p.title === "string" ? p.title : undefined);
  if (ref && ref.trim()) return `${ref.trim()} (v${s.version})`;
  const summary = (s.summary ?? (typeof p.summary === "string" ? p.summary : "")).trim();
  if (summary) {
    const words = summary.split(/\s+/);
    return `v${s.version}: ${words.slice(0, 6).join(" ")}${words.length > 6 ? "…" : ""}`;
  }
  return `v${s.version}`;
}

export type ScopePackEntry =
  | { kind: "direct"; label: string; href: string }
  | { kind: "ask"; label: string; base: string }
  | { kind: "none"; label: string };

/**
 * The single "scope + selections + lump sum" menu entry (Amir, 2026-10-27).
 * The estimate's own scope is used without asking, and named in the entry.
 * Only an estimate with no scope, on a project with several, asks which.
 */
export function scopePackEntry(pdfScope: PdfScope | null, base: string, cb: number): ScopePackEntry {
  const label = "Client: scope + selections + lump sum";
  if (!pdfScope || pdfScope.scope_count === 0) return { kind: "none", label: `${label} (add a scope first)` };
  if (pdfScope.scope)
    return {
      kind: "direct",
      label: `${label} (${scopeLabel(pdfScope.scope)})`,
      href: `${base}?mode=scopepack&scope_id=${pdfScope.scope.id}&_cb=${cb}`,
    };
  return { kind: "ask", label: `${label}…`, base: `${base}?mode=scopepack&_cb=${cb}` };
}
