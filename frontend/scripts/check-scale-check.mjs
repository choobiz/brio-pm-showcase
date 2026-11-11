/**
 * The measure page must say when a page's scale disagrees with the drawing
 * (456 W Sample ID-0, 2026-10-26: title block 1/4", plan enlarged to ~0.48"
 * per foot, every length 1.9x too long), and its calibrate box must take a
 * printed dimension as written (48'-5"), not only decimal feet.
 */
import { describeLengthError, describeScale, lengthError, parseFeetInput } from "../src/lib/scaleCheck.ts";

let bad = 0;
const eq = (name, got, want) => {
  const ok = typeof want === "number" && typeof got === "number" ? Math.abs(got - want) < 1e-3 : got === want;
  if (!ok) { bad++; console.error(`✗ ${name}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`); }
};

// ID-0: 1/4" in use (18), the plan measures 34.6 units per foot.
eq("ID-0 is flagged", lengthError(18, 34.6) !== null, true);
eq("ID-0 lengths are 92% too long", describeLengthError(lengthError(18, 34.6)), "92% too long");
eq("ID-0's real scale in words", describeScale(34.6), `0.48" = 1'-0"`);
eq("a sheet that agrees is not flagged", lengthError(18, 17.996), null);
eq("1:50 is not passed off as 1/4", lengthError(18, 17.28) !== null, true);
eq("half-size print reads too short", describeLengthError(lengthError(18, 9)), "50% too short");
eq("named scale", describeScale(18.05), `1/4" = 1'-0"`);
eq("metric sheet names metric", describeScale(17.28, "1:100"), "1:50");
eq("metric sheet, odd scale", describeScale(10.8, "1:50"), "1:80");
eq("no scale in use is not a mismatch", lengthError(0, 18), null);

for (const [raw, want] of [
  ["48.42", 48.42], [`48'-5"`, 48 + 5 / 12], [`48' 5"`, 48 + 5 / 12], ["48'5", 48 + 5 / 12],
  ["48-5", 48 + 5 / 12], ["48 5 1/2", 48 + 5.5 / 12], ["48'", 48], ["12", 12],
  ["", null], ["0", null], ["abc", null], [`5'-13"`, null], ["1/4", null],
]) eq(`parse ${JSON.stringify(raw)}`, parseFeetInput(raw), want);

if (bad) process.exit(1);
console.log("✓ the page flags a scale the drawing disagrees with, and calibrate takes 48'-5\"");
