import type { NextConfig } from "next";

/** Workspace tool → the project page that replaced it (UI round 1, phase 8).
 *  The tools carried the project as `?project=`. */
const WORKSPACE_TOOLS: Record<string, string> = {
  takeoff: "takeoff",
  report: "site",
  synthesis: "documents",
  drawings: "documents",
  drafts: "estimate",
  proposals: "overview",
  setup: "overview",
};

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // /sw.js is served by a route handler (src/app/sw.js/route.ts) so it gets a
  // JS MIME type + Service-Worker-Allowed. The manifest is served statically
  // (Chrome accepts its type).

  // Pages retired in phase 8 send old bookmarks to their replacement.
  // Temporary (307), since round 2 may move some of these again.
  async redirects() {
    const project = { type: "query" as const, key: "project", value: "(?<pid>[0-9a-f-]{36})" };
    return [
      ...Object.entries(WORKSPACE_TOOLS).map(([tool, page]) => ({
        source: `/workspace/${tool}`,
        has: [project],
        destination: `/projects/:pid/${page}`,
        permanent: false,
      })),
      { source: "/workspace/divisions", destination: "/divisions", permanent: false },
      { source: "/workspace", destination: "/dashboard", permanent: false },
      { source: "/workspace/projects/:id", destination: "/projects/:id/overview", permanent: false },
      { source: "/workspace/:path*", destination: "/projects", permanent: false },
      { source: "/projects/:id/workbench", destination: "/projects/:id/overview", permanent: false },
      { source: "/projects/:id/workbench/drawing-analysis", destination: "/projects/:id/takeoff", permanent: false },
      { source: "/projects/:id/workbench/estimates", destination: "/projects/:id/estimate", permanent: false },
      { source: "/projects/:id/workbench/estimates/:vid", destination: "/projects/:id/estimate/:vid", permanent: false },
      { source: "/projects/:id/daily-reports", destination: "/projects/:id/site", permanent: false },
      { source: "/projects/:id/hours", destination: "/projects/:id/site", permanent: false },
    ];
  },
};

export default nextConfig;
