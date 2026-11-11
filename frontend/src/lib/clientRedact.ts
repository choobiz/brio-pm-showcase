/**
 * Internal language kept out of anything a client reads: the client edition of
 * the estimate PDF and the app's "Client's view". Substring, case-insensitive.
 * Mirrors _CLIENT_REDACT_TERMS in backend/app/services/estimate_pdf.py; a
 * backend test keeps the two lists equal.
 */
export const CLIENT_REDACT_TERMS = [
  "contingency",
  "owner audit adjustment",
  "mobilization",
  "overhead",
  "profit",
  "margin",
  "markup",
  "calibration",
  "cad_extract",
  "discipline-scoped",
];

/** The bullets a client may read: empty and internal ones dropped. */
export function clientSafe(items: string[]): string[] {
  return items.filter((x) => {
    const t = x.trim().toLowerCase();
    return t && !CLIENT_REDACT_TERMS.some((term) => t.includes(term));
  });
}
