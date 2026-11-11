/**
 * Two detected groups on one page must never share a key: saved lengths are
 * stored under it, and a shared key let one tick overwrite another
 * (456 W Sample, 2026-10-26).
 */
import { candidateKey, pairRowsWithCandidates } from "../src/lib/candidateKey.ts";

const g = (bbox, extra = {}) => ({ page: 3, classification: "dimension_line", width_bucket: "thin", orthogonal: true, bbox, ...extra });
let bad = 0;
const a = candidateKey(g([100, 200, 400, 202])), b = candidateKey(g([900, 1200, 1300, 1202]));
if (a === b) { bad++; console.error(`✗ two groups of the same type and weight share a key: ${a}`); }
const reparsed = candidateKey(g([100.04, 199.96, 400.2, 202.1]));
if (reparsed !== a) { bad++; console.error(`✗ the same group after a re-parse got a new key: ${a} vs ${reparsed}`); }
if (candidateKey(g([100, 200, 400, 202], { page: 4 })) === a) { bad++; console.error("✗ the same outline on another page shares a key"); }
// Rows pair with their own group, not the first lookalike.
const groups = [240, 240, 1200].map((len, i) => ({ ...g([i * 100, 0, i * 100 + 50, 2]), total_length_pdf_units: len }));
const rows = groups.map((x) => ({ ...x, pdf_units: x.total_length_pdf_units }));
const paired = pairRowsWithCandidates(rows, groups);
if (paired[1] !== groups[1]) { bad++; console.error("✗ the second of two same-length rows was paired with the first group"); }
if (pairRowsWithCandidates(rows, groups.slice().reverse())[0] !== null) { bad++; console.error("✗ a row was paired with a group that does not match it"); }
if (bad) process.exit(1);
console.log("✓ every detected group has its own key and its own row (5 cases)");
