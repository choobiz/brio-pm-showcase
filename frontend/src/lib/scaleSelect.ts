// No imports: scripts/check-scale-select.mjs loads this file directly in Node.

export const NAMED_SCALES: { label: string; group: string }[] = [
  { label: '1/8" = 1\'-0"', group: "Imperial" },
  { label: '3/16" = 1\'-0"', group: "Imperial" },
  { label: '1/4" = 1\'-0"', group: "Imperial" },
  { label: '3/8" = 1\'-0"', group: "Imperial" },
  { label: '1/2" = 1\'-0"', group: "Imperial" },
  { label: '3/4" = 1\'-0"', group: "Imperial" },
  { label: '1" = 1\'-0"', group: "Imperial" },
  { label: "1:20", group: "Metric" },
  { label: "1:25", group: "Metric" },
  { label: "1:50", group: "Metric" },
  { label: "1:75", group: "Metric" },
  { label: "1:100", group: "Metric" },
  { label: "1:200", group: "Metric" },
];

export const CUSTOM_SCALE = "__custom";

/** What a page's scale dropdown shows: the named scale in use, CUSTOM_SCALE
 * for a calibrated or otherwise unnamed scale, or "" ("Set scale…") when the
 * page has none. It was hard-wired to "", so a page with a saved scale still
 * read "Set scale…" (Amir, 2026-10-26). */
export function scaleSelectValue(eff: { ppf: number | null; label: string | null } | undefined): string {
  if (!eff?.ppf) return "";
  const norm = (x: string) => x.replace(/\s+/g, "").toLowerCase();
  const label = eff.label;
  const named = label ? NAMED_SCALES.find((n) => norm(n.label) === norm(label)) : undefined;
  return named ? named.label : CUSTOM_SCALE;
}
