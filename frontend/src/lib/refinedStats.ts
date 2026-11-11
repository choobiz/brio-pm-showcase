// No imports: scripts/check-refined-stats.mjs loads this file directly in Node.

type PathLike = { bbox: number[]; length: number };
type CandidateLike = { total_length_pdf_units: number; path_count: number; paths: PathLike[] };

/** A stroke's identity inside its group, stable across reloads. */
export function pathId(p: PathLike): string {
  return [
    Math.round(p.bbox[0]),
    Math.round(p.bbox[1]),
    Math.round(p.bbox[2]),
    Math.round(p.bbox[3]),
  ].join("_");
}

/**
 * Included count and real feet for a group after the user removed some
 * strokes from it.
 *
 * The server stores only the 60 longest strokes of a group (for highlighting),
 * so the length must start from the group's full total and subtract what was
 * removed. It used to add up the stored strokes, so any group of more than 60
 * read short, and ticking it saved the short figure (456 W Sample ID-1,
 * 2026-10-26: the 194 ft washroom-core wall showed 174 ft).
 */
export function refinedStats(
  candidate: CandidateLike,
  pdfUnitsPerFoot: number | null | undefined,
  excluded: Set<string> | undefined,
): { includedCount: number; totalCount: number; realFeet: number | null } {
  const totalCount = Math.max(candidate.path_count, candidate.paths.length);
  let removedLen = 0;
  let removed = 0;
  if (excluded && excluded.size > 0) {
    for (const p of candidate.paths) {
      if (excluded.has(pathId(p))) {
        removed++;
        removedLen += p.length;
      }
    }
  }
  const lenPdf = Math.max(0, candidate.total_length_pdf_units - removedLen);
  return {
    includedCount: totalCount - removed,
    totalCount,
    realFeet: pdfUnitsPerFoot && pdfUnitsPerFoot > 0 ? lenPdf / pdfUnitsPerFoot : null,
  };
}
