/**
 * Every PDF mode a menu entry asks for must reach the backend. 2026-10-27:
 * the proxy didn't know "divisions", so "Client: by division" downloaded the
 * material-selection PDF instead, with prices and no breakdown.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { SELECTION_PDF_MODES, selectionPdfMode } from "../src/lib/selectionPdfModes.ts";

let bad = 0;
const fail = (m) => { bad++; console.error(`✗ ${m}`); };

// Every mode= used with the selection-pdf route anywhere in the app.
const used = new Set();
const walk = (d) => {
  for (const f of readdirSync(d)) {
    const p = join(d, f);
    if (statSync(p).isDirectory()) { if (f !== "node_modules" && f !== ".next") walk(p); continue; }
    if (!/\.tsx?$/.test(f)) continue;
    const src = readFileSync(p, "utf8");
    for (const m of src.matchAll(/estimate-selection-pdf\/[^`"']*\?mode=([a-z]+)/g)) used.add(m[1]);
  }
};
walk("src");
for (const m of used) if (!SELECTION_PDF_MODES.includes(m)) fail(`a link asks for mode=${m}, which the proxy turns into "selection"`);
if (selectionPdfMode("divisions") !== "divisions") fail("divisions is dropped");
if (selectionPdfMode("divisions_lines") !== "divisions_lines") fail("divisions_lines is dropped (Client: by division, with line items)");
if (selectionPdfMode("bogus") !== "selection" || selectionPdfMode(null) !== "selection") fail("unknown modes must fall back to selection");
const route = readFileSync("src/app/api/workbench/estimate-selection-pdf/[pid]/[vid]/route.ts", "utf8");
if (!route.includes("selectionPdfMode(")) fail("the proxy does not use selectionPdfMode");
if (bad) process.exit(1);
console.log(`✓ every selection-pdf mode used in the app reaches the backend (${[...used].sort().join(", ")})`);
