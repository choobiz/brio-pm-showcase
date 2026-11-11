/**
 * Dictation must not write words twice (Amir, 2026-10-16: "It's double-writing
 * the words, and it just wasn't usable"). Replays the result sequences two real
 * browsers send and checks the transcript.
 */
import { transcriptFrom } from "../src/lib/speechText.ts";

const r = (t, isFinal = true) => ({ isFinal, 0: { transcript: t } });
const cases = [
  ["desktop: separate finals", [r("Forms stripped on the east footing."), r(" Concrete truck late 40 minutes.")],
   "Forms stripped on the east footing. Concrete truck late 40 minutes."],
  ["Android: cumulative finals", [r("forms stripped"), r("forms stripped on the east"), r("forms stripped on the east footing")],
   "forms stripped on the east footing"],
  ["Android: cumulative then a new sentence", [r("four on site"), r("four on site today"), r("truck was late")],
   "four on site today truck was late"],
  ["a final repeated verbatim", [r("poured footings"), r("poured footings")], "poured footings"],
];
let bad = 0;
for (const [name, results, want] of cases) {
  const got = transcriptFrom(results).final;
  if (got !== want) { bad++; console.error(`✗ ${name}: got "${got}", want "${want}"`); }
}
const live = transcriptFrom([r("forms stripped"), r("forms stripped on the", false)]);
if (live.final !== "forms stripped" || live.interim !== "on the") { bad++; console.error(`✗ interim restating the final: ${JSON.stringify(live)}`); }
if (bad) process.exit(1);
console.log(`✓ dictation writes each word once (${cases.length + 1} sequences)`);
