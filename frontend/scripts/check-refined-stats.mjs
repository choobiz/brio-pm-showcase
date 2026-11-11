/**
 * A detected group's length is its full total, not the sum of the 60 strokes
 * the server keeps for highlighting (456 W Sample ID-1, 2026-10-26: a 194 ft
 * wall read and would have saved as 174 ft).
 */
import { pathId, refinedStats } from "../src/lib/refinedStats.ts";

let bad = 0;
const eq = (name, got, want) => {
  if (Math.abs(got - want) > 1e-6) { bad++; console.error(`✗ ${name}: got ${got}, want ${want}`); }
};
// 95 strokes of 36.85 units = 3500.75 total; only 60 stored.
const paths = Array.from({ length: 60 }, (_, i) => ({ bbox: [i * 10, 0, i * 10 + 5, 9], length: 36.85 }));
const cand = { total_length_pdf_units: 3500.75, path_count: 95, paths };
eq("untouched group = its full length", refinedStats(cand, 18, undefined).realFeet, 3500.75 / 18);
eq("untouched group counts all strokes", refinedStats(cand, 18, new Set()).totalCount, 95);
const ex = new Set([pathId(paths[0]), pathId(paths[1])]);
eq("removing 2 strokes subtracts just those", refinedStats(cand, 18, ex).realFeet, (3500.75 - 2 * 36.85) / 18);
eq("included count", refinedStats(cand, 18, ex).includedCount, 93);
const small = { total_length_pdf_units: 100, path_count: 2, paths: [{ bbox: [0, 0, 60, 1], length: 60 }, { bbox: [0, 5, 40, 6], length: 40 }] };
eq("small group unchanged", refinedStats(small, 10, undefined).realFeet, 10);
eq("no scale, no feet", refinedStats(small, null, undefined).realFeet === null ? 1 : 0, 1);
if (bad) process.exit(1);
console.log("✓ a group's length is its full total, not just the strokes kept for highlighting");
