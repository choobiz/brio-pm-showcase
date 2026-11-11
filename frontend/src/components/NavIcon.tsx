/**
 * Nav icons, one small outline set shared by both sidebars.
 *
 * A collapsed sidebar is only useful if the icons are distinguishable at a
 * glance — otherwise it trades width for a guessing game. These are chosen to
 * be different SHAPES rather than different details: a page, a folder, a
 * ruler, a calendar read apart at 18px in a way that five variations on
 * "document" would not.
 */
const PATHS: Record<string, string> = {
  dashboard: "M3 12h7V3H3v9Zm0 9h7v-6H3v6Zm11 0h7V12h-7v9Zm0-18v6h7V3h-7Z",
  // A warning triangle. Nothing else in this set is triangular, so the one
  // destination that means "something needs you" is the one shape you can
  // pick out of a collapsed rail without reading.
  register: "M12 3.5 2 20.5h20L12 3.5Zm0 5.5v6m0 3h.01",
  projects: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z",
  clients: "M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 10a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm11 9v-1a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8",
  analytics: "M4 20V10m5 10V4m5 16v-7m5 7V8",
  rates: "M12 3v18M8 7h6a3 3 0 0 1 0 6H8h7a3 3 0 0 1 0 6H8",
  trades: "M14 7l-1.5-1.5a3.5 3.5 0 0 0-5 5L4 14l6 6 3.5-3.5a3.5 3.5 0 0 0 5-5L17 10",
  skills: "M12 3 3 8l9 5 9-5-9-5Zm-9 8 9 5 9-5M3 15l9 5 9-5",
  reviews: "M12 4l2.3 4.9 5.2.7-3.8 3.8.9 5.3-4.6-2.6-4.6 2.6.9-5.3L4.5 9.6l5.2-.7L12 4Z",
  users: "M17 20v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M10 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm7 0 2 2 4-4",
  // A funnel: nothing else in this set narrows downward, so it stays
  // legible at 18px next to the two people-shaped icons.
  crm: "M3 5h18l-7 8v6l-4 2v-8L3 5Z",
  overview: "M4 5h16M4 12h16M4 19h10",
  documents: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Zm0 0v5h5",
  index: "M5 4h14v16H5zM9 8h6M9 12h6M9 16h4",
  scope: "M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 1 0 4 0M9 5a2 2 0 1 1 4 0m-4 8h6m-6 4h6",
  drawing: "M3 20h18M6 20V9l6-5 6 5v11M10 20v-5h4v5",
  estimates: "M7 3h10a2 2 0 0 1 2 2v16l-7-4-7 4V5a2 2 0 0 1 2-2Z",
  schedule: "M4 6h16v14H4zM4 10h16M8 3v4m8-4v4",
  reports: "M6 3h9l4 4v14H6zM9 12h7M9 16h5",
  hours: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-14v5l3 2",
  cash: "M3 7h18v10H3zM12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z",
  sov: "M4 5h16M4 12h16M4 19h16M9 5v14",
  change: "M4 8h12l-3-3m7 11H8l3 3",
  invoices: "M6 3h12v18l-3-2-3 2-3-2-3 2V3ZM9 8h6M9 12h6",
  contract: "M8 4h8l4 4v12H8zM8 4H6a2 2 0 0 0-2 2v12M12 12h4M12 16h4",
  narratives: "M4 5h16v10H8l-4 4V5Z",
  presentation: "M3 4h18v11H3zM12 15v5M8 20h8",
  selections: "M9 12l2 2 4-5M5 4h14v16H5z",
  messages: "M4 5h16v11H9l-5 4V5Z",
  conversations: "M8 4h12v10H12l-4 3v-3H8V4ZM4 8v9l3-2",
};

export function NavIcon({ name, className = "h-4 w-4" }: { name: string; className?: string }) {
  const d = PATHS[name] ?? PATHS.documents;
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}
