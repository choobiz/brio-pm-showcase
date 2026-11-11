/**
 * The "scope + selections + lump sum" PDF is ONE menu entry. It uses the
 * estimate's own scope without asking (456 W Sample: the corridor estimate
 * belongs to the corridor scope), names it, and asks only when the estimate
 * has no scope and the project has several (Amir, 2026-10-27).
 */
import { scopeLabel, scopePackEntry } from "../src/lib/scopePackLinks.ts";

let bad = 0;
const ok = (name, cond) => { if (!cond) { bad++; console.error(`✗ ${name}`); } };
const base = "/api/workbench/estimate-selection-pdf/p/v";
const corridor = { id: "22222222-2222-2222-2222-222222222222", version: 2, reference: "456SA-4FL-CORR-8-2291", summary: "" };

const own = scopePackEntry({ scope: corridor, scope_count: 2 }, base, 1);
ok("linked estimate opens directly with its own scope", own.kind === "direct" && own.href.includes(`scope_id=${corridor.id}`));
ok("the entry names the scope it uses", own.label.includes("456SA-4FL-CORR-8-2291 (v2)"));
ok("unlinked estimate on a multi-scope project asks", scopePackEntry({ scope: null, scope_count: 2 }, base, 1).kind === "ask");
ok("no scope: entry says to add one", scopePackEntry({ scope: null, scope_count: 0 }, base, 1).kind === "none");
ok("server unreachable: no broken link", scopePackEntry(null, base, 1).kind === "none");
ok("label falls back to the version", scopeLabel({ version: 3 }) === "v3");
ok("label from summary", scopeLabel({ version: 1, summary: "Suite 100 fit-out with new demising wall and partitions" }) === "v1: Suite 100 fit-out with new demising…");
if (bad) process.exit(1);
console.log("✓ scope + selections + lump sum: one entry, own scope, asks only when it must (7 cases)");
