// Shared rollup arithmetic for estimate edits. Mirrors the Python rollup
// used by the brio-admin build scripts so the in-browser editor produces
// payloads structurally identical to what the JSON-based pipeline writes.
//
// Pattern: every category has items[]; one item per category is marked
// `contingency: true` and is the auto-generated `Trade contingency @ X%`
// line. We strip those before summing, recompute, then re-append.

export type EstimateItem = {
  name: string;
  qty?: number | string;
  unitCost?: number;
  laborHours?: number;
  labor?: number;
  materials?: number;
  contingency?: boolean;
};

export type EstimateCategory = {
  name: string;
  description?: string;
  contingencyPct?: number;
  optional?: boolean;
  items?: EstimateItem[];
  // computed:
  subtotalRaw?: number;
  contingencyAmount?: number;
  total?: number;
};

export type EstimatePayload = {
  id?: string;
  reference?: string;
  date?: string;
  detailLevel?: string;
  overhead?: number;
  profit?: number;
  markupLabel?: string;
  notes?: string;
  discussionPoints?: { title?: string; body?: string; computed?: string }[];
  categories: EstimateCategory[];
};

/** Sum labor + materials across non-contingency items. */
export function categoryRaw(cat: EstimateCategory): number {
  return (cat.items ?? [])
    .filter((it) => !it.contingency)
    .reduce((s, it) => s + (Number(it.labor) || 0) + (Number(it.materials) || 0), 0);
}

/** Recompute one category in place: strip + re-append the contingency line. */
export function recomputeCategory(cat: EstimateCategory): EstimateCategory {
  const items = (cat.items ?? []).filter((it) => !it.contingency);
  const pct = cat.contingencyPct ?? 5;

  // Standard/summary categories carry their cost in `subtotalRaw`/`total` with NO
  // line items. Summing an empty items[] would zero them out on edit — fall back
  // to the stored base (mirrors the backend rollup in _persist/_rollup_category).
  if (items.length === 0) {
    const raw = Number(cat.subtotalRaw) || Number(cat.total) || 0;
    const cont = Math.round((raw * pct) / 100);
    return { ...cat, items, subtotalRaw: raw, contingencyAmount: cont, total: raw + cont };
  }

  const raw = items.reduce(
    (s, it) => s + (Number(it.labor) || 0) + (Number(it.materials) || 0),
    0,
  );
  const cont = Math.round(raw * pct / 100);
  items.push({
    name: `Trade contingency @ ${pct}%`,
    qty: 1,
    unitCost: cont,
    labor: 0,
    materials: cont,
    laborHours: 0,
    contingency: true,
  });
  return {
    ...cat,
    items,
    subtotalRaw: raw,
    contingencyAmount: cont,
    total: raw + cont,
  };
}

/** Recompute every category + return whole-payload rollups. */
export function recomputePayload(p: EstimatePayload): {
  payload: EstimatePayload;
  baseDirectCont: number;
  optDirectCont: number;
  oh: number;
  baseTotalPretax: number;
  optTotalPretax: number;
  combinedPretax: number;
} {
  const categories = (p.categories ?? []).map(recomputeCategory);
  const overhead = Number(p.overhead) || 0;
  const profit = Number(p.profit) || 0;

  const sumOf = (filterFn: (c: EstimateCategory) => boolean) => {
    const cats = categories.filter(filterFn);
    const direct = cats.reduce((s, c) => s + (c.subtotalRaw ?? 0), 0);
    const cont = cats.reduce((s, c) => s + (c.contingencyAmount ?? 0), 0);
    const sub = direct + cont;
    const oh = sub * (overhead / 100);
    const pr = (sub + oh) * (profit / 100);
    return { sub, oh, pr, total: sub + oh + pr };
  };

  const base = sumOf((c) => !c.optional);
  const opt = sumOf((c) => Boolean(c.optional));

  return {
    payload: { ...p, categories },
    baseDirectCont: base.sub,
    optDirectCont: opt.sub,
    oh: base.oh + opt.oh,
    baseTotalPretax: base.total,
    optTotalPretax: opt.total,
    combinedPretax: base.total + opt.total,
  };
}
