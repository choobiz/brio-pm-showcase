/**
 * No workspace file may put a raw response body into a thrown Error.
 *
 * The pattern this forbids:
 *
 *     if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
 *
 * It is harmless while the body is our own JSON and actively harmful when it is
 * not. On 2 Oct the API container restarted; the body was nginx's error PAGE;
 * the register rendered `Error: API 502: <html> <head><title>502 Bad Gateway…`
 * in a red box, with no hint that the fix was to wait twenty seconds. Markup
 * where a sentence belonged.
 *
 * Workspace calls go through `workspaceApi`, which uses a body only when it is
 * JSON carrying a `detail` we wrote, and keeps everything else as `technical`.
 *
 * ⚠️ SCOPE IS DELIBERATELY NARROW. The same throw appears in ~25 live-app libs.
 * Those are NOT checked here, because fixing them means editing the app the team
 * uses today and that is outside the preview build's agreed scope. This guard
 * stops the pattern coming BACK into the workspace; it does not claim the rest
 * of the app is clean. Widen `ROOTS` when the workspace is adopted.
 *
 * This is a static check, not a test of the parsing. It cannot tell you that
 * `workspaceApi` classifies a 502 correctly — only that nobody re-introduced the
 * shortcut that made the classification moot.
 */
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";

// /workspace was retired in UI round 1 phase 8 (2026-10-13); the round-1
// pages that replaced it use the same client, so the guard moved with it.
const P = "src/app/(authed)/projects/[id]";
const ROOTS = [
  "src/app/(authed)/dashboard",
  "src/app/(authed)/divisions",
  "src/app/(authed)/ai-setup",
  ...["overview", "documents", "scope", "takeoff", "estimate", "site", "money", "prepare"].map((d) => `${P}/${d}`),
];
const LIBS = [
  "src/lib/workspaceApi.ts",
  "src/lib/register.ts",
  "src/lib/documentSearch.ts",
  "src/lib/drawingRegister.ts",
  "src/lib/divisions.ts",
  "src/lib/workspaceDailyCapture.ts",
  "src/lib/workspaceDrafts.ts",
  "src/lib/workspaceInterview.ts",
  "src/lib/home.ts",
  "src/lib/takeoff.ts",
  "src/lib/site.ts",
  "src/lib/money.ts",
  "src/lib/finish.ts",
  "src/lib/prep.ts",
  "src/lib/assistant.ts",
];

const walk = (dir, out = []) => {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.tsx?$/.test(name)) out.push(p);
  }
  return out;
};

// `res.text()` / `r.text()` interpolated into a template literal that is thrown,
// and the String(e) shortcut that renders "Error: <html>…" just as readably.
const BANNED = [
  [/throw new Error\([^)]*\.text\(\)/s, "throws a raw response body"],
  [/set(?:Err|Error)\(String\(/, "stringifies a caught error into state"],
];

// Every lib is checked since 2026-10-14: the round-1 pages call the live-app
// libs (projects, scopes, documentSelection...), and one of them put nginx's
// 502 page on the Scope page. apiError.ts is the shared, safe way to fail.
const files = [...ROOTS.flatMap((r) => walk(r)), ...walk("src/lib")];
const problems = [];

for (const file of files) {
  // workspaceApi is the one place allowed to read a body — that is its job.
  if (file.endsWith("workspaceApi.ts")) continue;
  const text = readFileSync(file, "utf8");
  for (const [re, why] of BANNED) {
    if (re.test(text)) problems.push(`${file}\n    ${why}`);
  }
}

if (problems.length) {
  console.error(
    `\n✗ ${problems.length} workspace file(s) surface a raw error to the user.\n` +
      `  A 502 body is an HTML page; rendering it tells a reader nothing and hides\n` +
      `  that the right response is to retry. Use workspaceGet/workspacePost from\n` +
      `  @/lib/workspaceApi and render with <WorkspaceError>.\n\n  ` +
      problems.join("\n  ") +
      "\n",
  );
  process.exit(1);
}
console.log(`✓ no workspace file surfaces a raw error body (${files.length} checked)`);
