/**
 * A server component may not import a client-only API lib.
 *
 * The libs under src/lib that declare `const BASE = "/api/backend"` fetch a
 * RELATIVE url. That works in the browser and throws in Node — "Failed to parse
 * URL from /api/backend/..." — so a server component importing one fails at
 * request time, not at build time, and only on the page that uses it.
 *
 * It happened on the estimates page: the scope list threw, the page caught the
 * throw to an empty array, and the UI told the user their project had no scope
 * of work. A build-time check is cheap; that failure cost an afternoon.
 *
 * Server-side data fetching belongs in a lib that uses `api` from `src/lib/api.ts`,
 * which builds an absolute URL and forwards the session cookie.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const walk = (dir, out = []) => {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.tsx?$/.test(name)) out.push(p);
  }
  return out;
};

// ⚠️ DETECTION IS TRANSITIVE, and it has to be.
//
// This used to look only for the literal `const BASE = "/api/backend"`. That
// held while every lib built its own fetch, and broke the moment one stopped:
// the workspace libs moved their transport into `workspaceApi`, the literal
// left those files, and four libs silently dropped out of this check while it
// still reported success. A guard that quietly covers less than it did is worse
// than no guard, because the green line is read as proof.
//
// So a lib is client-only if it declares the relative BASE *or* imports another
// lib that is. Seeded from the literal, then closed over imports until stable.
const libFiles = walk("src/lib");
const libName = (f) => f.replace(/^src\/lib\//, "").replace(/\.tsx?$/, "");

const clientLibs = new Set(
  libFiles
    .filter((f) => readFileSync(f, "utf8").includes('const BASE = "/api/backend"'))
    .map(libName),
);

for (let changed = true; changed; ) {
  changed = false;
  for (const f of libFiles) {
    const name = libName(f);
    if (clientLibs.has(name)) continue;
    const text = readFileSync(f, "utf8");
    for (const dep of clientLibs) {
      if (text.includes(`from "@/lib/${dep}"`)) {
        clientLibs.add(name);
        changed = true;
        break;
      }
    }
  }
}

const problems = [];
for (const file of walk("src/app")) {
  const text = readFileSync(file, "utf8");
  const isClient = /^\s*['"]use client['"]/.test(text);
  if (isClient) continue;
  for (const lib of clientLibs) {
    if (text.includes(`from "@/lib/${lib}"`)) {
      problems.push(`${file}\n    imports @/lib/${lib}, which fetches a relative URL`);
    }
  }
}

if (problems.length) {
  console.error(
    `\n✗ ${problems.length} server component(s) import a client-only API lib.\n` +
      `  A relative fetch throws in Node at request time, not here.\n` +
      `  Move the call to a lib using \`api\` from src/lib/api.ts, or mark the file "use client".\n\n  ` +
      problems.join("\n  ") +
      "\n",
  );
  process.exit(1);
}
console.log(`✓ no server component imports a client-only API lib (${clientLibs.size} checked)`);
