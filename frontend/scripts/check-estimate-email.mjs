/**
 * The client email for estimate packages (Amir, 2026-10-27): starts with the
 * estimate being viewed plus the newest estimate of each other scope ticked
 * (456: corridor v008 + suite v007), and opens in the mail app intact.
 */
import { defaultPicks, mailtoHref } from "../src/lib/estimateEmail.ts";

let bad = 0;
const ok = (n, c) => { if (!c) { bad++; console.error(`✗ ${n}`); } };
const o = (id, v, scope) => ({ id, version_number: v, reference: "", is_active: false, title: "", scope_reference: scope, subtotal: 1, total: 1 });
const opts = [o("v8", 8, "CORR"), o("v7", 7, "STE"), o("v6", 6, "CORR"), o("v3", 3, "STE"), o("v1", 1, null)];

const p = defaultPicks(opts, "v8").sort();
ok("viewing corridor v8 ticks it and suite v7", JSON.stringify(p) === JSON.stringify(["v7", "v8"]));
ok("viewing an older corridor keeps it and adds the newest suite", JSON.stringify(defaultPicks(opts, "v6").sort()) === JSON.stringify(["v6", "v7"]));
ok("an unlinked estimate alone", JSON.stringify(defaultPicks([o("x", 1, null)], "x")) === JSON.stringify(["x"]));
const href = mailtoHref("456 West Sample: estimates", "Hi [name],\n\n$188,048.30 + GST & more");
ok("mailto keeps line breaks and symbols", decodeURIComponent(href.split("body=")[1]) === "Hi [name],\n\n$188,048.30 + GST & more");
if (bad) process.exit(1);
console.log("✓ estimate email: default picks and mailto (4 cases)");
