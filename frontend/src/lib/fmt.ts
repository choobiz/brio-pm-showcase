// Client-safe formatters. Kept separate from workbench.ts so the in-browser
// editor can import these without dragging in `next/headers` from api.ts.

export const fmtMoney = (n: number | null | undefined) => {
  if (n == null) return "—";
  const abs = Math.abs(n);
  const formatted = abs.toLocaleString("en-CA", {
    style: "currency",
    currency: "CAD",
    maximumFractionDigits: 0,
  });
  return n < 0 ? `(${formatted})` : formatted;
};

export const fmtDate = (s: string | null | undefined) => {
  if (!s) return "—";
  return new Date(s).toLocaleDateString("en-CA", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
};
