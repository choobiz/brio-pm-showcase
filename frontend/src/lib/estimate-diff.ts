// Diff two estimate payloads. Identity for line items is positional within a
// category (items[i] in A maps to items[i] in B). Categories are matched by
// `name` — if a category is renamed, the diff shows it as removed + added.

import type {
  EstimateCategory,
  EstimateItem,
  EstimatePayload,
} from "./estimate-rollup";
import { recomputePayload } from "./estimate-rollup";

export type ItemDiff =
  | { kind: "unchanged"; left: EstimateItem; right: EstimateItem; idx: number }
  | { kind: "changed"; left: EstimateItem; right: EstimateItem; idx: number; delta: number }
  | { kind: "added"; right: EstimateItem; idx: number }
  | { kind: "removed"; left: EstimateItem; idx: number };

export type CategoryDiff = {
  name: string; // canonical category name (right's if present, else left)
  leftCat?: EstimateCategory;
  rightCat?: EstimateCategory;
  kind: "unchanged" | "changed" | "added" | "removed";
  leftTotal: number;
  rightTotal: number;
  delta: number;
  items: ItemDiff[];
};

export type EstimateDiff = {
  categories: CategoryDiff[];
  leftPretax: number;
  rightPretax: number;
  pretaxDelta: number;
  leftOverhead: number;
  rightOverhead: number;
};

function itemTotal(it: EstimateItem): number {
  return (Number(it.labor) || 0) + (Number(it.materials) || 0);
}

function itemsEqual(a: EstimateItem, b: EstimateItem): boolean {
  return (
    a.name === b.name &&
    Number(a.qty || 0) === Number(b.qty || 0) &&
    Number(a.unitCost || 0) === Number(b.unitCost || 0) &&
    Number(a.labor || 0) === Number(b.labor || 0) &&
    Number(a.materials || 0) === Number(b.materials || 0)
  );
}

function diffCategoryItems(
  left: EstimateItem[] | undefined,
  right: EstimateItem[] | undefined,
): ItemDiff[] {
  const L = (left ?? []).filter((i) => !i.contingency);
  const R = (right ?? []).filter((i) => !i.contingency);
  const n = Math.max(L.length, R.length);
  const out: ItemDiff[] = [];
  for (let i = 0; i < n; i++) {
    const l = L[i];
    const r = R[i];
    if (l && r) {
      if (itemsEqual(l, r)) {
        out.push({ kind: "unchanged", left: l, right: r, idx: i });
      } else {
        out.push({
          kind: "changed",
          left: l,
          right: r,
          idx: i,
          delta: itemTotal(r) - itemTotal(l),
        });
      }
    } else if (r && !l) {
      out.push({ kind: "added", right: r, idx: i });
    } else if (l && !r) {
      out.push({ kind: "removed", left: l, idx: i });
    }
  }
  return out;
}

export function diffPayloads(leftIn: EstimatePayload, rightIn: EstimatePayload): EstimateDiff {
  // Recompute both sides so subtotals are coherent (handles old data shapes)
  const left = recomputePayload(leftIn);
  const right = recomputePayload(rightIn);

  const lCats = left.payload.categories ?? [];
  const rCats = right.payload.categories ?? [];

  // Map by name. Anything in both is paired; lone entries are added/removed.
  const lByName = new Map(lCats.map((c) => [c.name, c]));
  const rByName = new Map(rCats.map((c) => [c.name, c]));
  const allNames = Array.from(new Set([...lByName.keys(), ...rByName.keys()]));

  // Order: preserve right's order first (the more recent version), then any
  // categories that exist only on the left (removed).
  const orderedNames = [
    ...rCats.map((c) => c.name),
    ...lCats.filter((c) => !rByName.has(c.name)).map((c) => c.name),
  ].filter((n, i, arr) => arr.indexOf(n) === i);

  void allNames; // (kept for future name-set asserts)

  const categories: CategoryDiff[] = orderedNames.map((name) => {
    const leftCat = lByName.get(name);
    const rightCat = rByName.get(name);
    const leftTotal = leftCat?.total ?? 0;
    const rightTotal = rightCat?.total ?? 0;
    const items = diffCategoryItems(leftCat?.items, rightCat?.items);

    let kind: CategoryDiff["kind"];
    if (!leftCat) kind = "added";
    else if (!rightCat) kind = "removed";
    else if (leftTotal === rightTotal && items.every((i) => i.kind === "unchanged"))
      kind = "unchanged";
    else kind = "changed";

    return {
      name,
      leftCat,
      rightCat,
      kind,
      leftTotal,
      rightTotal,
      delta: rightTotal - leftTotal,
      items,
    };
  });

  return {
    categories,
    leftPretax: left.combinedPretax,
    rightPretax: right.combinedPretax,
    pretaxDelta: right.combinedPretax - left.combinedPretax,
    leftOverhead: Number(left.payload.overhead) || 0,
    rightOverhead: Number(right.payload.overhead) || 0,
  };
}
