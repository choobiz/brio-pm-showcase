// No imports: scripts/check-candidate-key.mjs loads this file directly in Node.

/** Identifies one detected group on a drawing; saved lengths are stored under it.
 *
 * It used to be page + type + line weight only. Since wall runs are chained
 * into one group each, a page holds many groups of the same type and weight,
 * so they shared a key: clicking one highlighted another, and ticking a second
 * one overwrote the first saved length (456 W Sample, 2026-10-26: 122 groups
 * on one sheet, a handful of keys). The group's rounded outline makes it unique
 * and survives a re-parse of the same file. */
export function candidateKey(c: {
  page: number;
  classification: string;
  width_bucket: string;
  orthogonal: boolean;
  bbox: [number, number, number, number] | number[];
}): string {
  const box = c.bbox.map((v) => Math.round(v)).join(",");
  return `${c.page}:${c.classification}:${c.width_bucket}:${c.orthogonal ? "o" : "d"}:${box}`;
}

type Group = { page: number; classification: string; width_bucket: string; orthogonal: boolean; total_length_pdf_units: number };
type Row = { page: number; classification: string; width_bucket: string; orthogonal: boolean; pdf_units: number };

/** The group each summary row describes, by position: the server writes one
 * row per group, in the same order. A row whose group does not agree on page,
 * type, weight and length gets null (shown nowhere) rather than a lookalike. */
export function pairRowsWithCandidates<G extends Group>(rows: Row[], groups: G[]): (G | null)[] {
  return rows.map((r, i) => {
    const g = groups[i];
    const same =
      g &&
      g.page === r.page &&
      g.classification === r.classification &&
      g.width_bucket === r.width_bucket &&
      g.orthogonal === r.orthogonal &&
      Math.abs(g.total_length_pdf_units - r.pdf_units) < 0.5;
    return same ? g : null;
  });
}
