/**
 * A page with a saved scale must show it in the scale dropdown, not "Set
 * scale…" (Amir, 2026-10-26: he picked 1/4" = 1'-0" and the page still asked
 * him to set the scale).
 */
import { CUSTOM_SCALE, scaleSelectValue } from "../src/lib/scaleSelect.ts";

const cases = [
  ["no scale", undefined, ""],
  ["scale row without a conversion", { ppf: null, label: null }, ""],
  ["picked from the list", { ppf: 18, label: `1/4" = 1'-0"` }, `1/4" = 1'-0"`],
  ["read from the title block, no spaces", { ppf: 18, label: `1/4"=1'-0"` }, `1/4" = 1'-0"`],
  ["metric", { ppf: 6.1, label: "1:50" }, "1:50"],
  ["calibrated", { ppf: 17.6, label: "calibrated (12.00 ft ref)" }, CUSTOM_SCALE],
  ["a scale with no label", { ppf: 18, label: null }, CUSTOM_SCALE],
];
let bad = 0;
for (const [name, eff, want] of cases) {
  const got = scaleSelectValue(eff);
  if (got !== want) { bad++; console.error(`✗ ${name}: got "${got}", want "${want}"`); }
}
if (bad) process.exit(1);
console.log(`✓ the scale dropdown shows the scale in use (${cases.length} cases)`);
