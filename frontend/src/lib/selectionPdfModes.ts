// No imports: scripts/check-selection-pdf-modes.mjs loads this file directly in Node.

/**
 * Modes the selection-pdf proxy passes to the backend. Anything else falls
 * back to "selection". 2026-10-27: "divisions" was missing here, so the
 * by-division PDF silently came back as the material-selection PDF (with its
 * allowance prices and no division breakdown).
 */
export const SELECTION_PDF_MODES = ["selection", "detailed", "lumpsum", "scopepack", "divisions", "divisions_lines"] as const;
export type SelectionPdfMode = (typeof SELECTION_PDF_MODES)[number];

export function selectionPdfMode(raw: string | null): SelectionPdfMode {
  return (SELECTION_PDF_MODES as readonly string[]).includes(raw ?? "") ? (raw as SelectionPdfMode) : "selection";
}
