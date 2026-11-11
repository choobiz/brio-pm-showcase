/**
 * Plain words for where a library rate came from, the same on the estimate
 * page and the Rate Library. A composed rate is a starter or a July draft only
 * when the starter library wrote it; the two hand-entered originals are not.
 */
export function rateSourceWords(source?: string | null, confidence?: string | null, sourceRef?: string | null): string {
  if (source === "own") return "your rate";
  if (source === "composed") {
    if (sourceRef?.includes("(july-draft)")) return "July draft";
    if (sourceRef?.includes("(starter)")) return "starter rate";
    return "composed";
  }
  if (source === "subcontract_derived") return "from a subcontract";
  if (source === "workbc") return "Job Bank wage";
  if (source === "published") return "published";
  if (source === "market") return "market";
  return source ?? "library";
}
