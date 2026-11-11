"""Re-derive an estimate payload's category totals from its line items.

The AI editor is forbidden from writing a total, so after any edit something
has to recompute them. Without this, correcting one line item's labour cost
leaves a category total that no longer equals the lines beneath it, and a PDF
that adds up two different ways depending on which number you read.

This is the generator's own rollup, lifted out so the edit path and the
generation path cannot drift into computing money differently.
"""

from __future__ import annotations

from typing import Any


def contingency_pct_for(cat: dict[str, Any], by_division: dict[str, float] | None) -> float:
    """The contingency percentage for a category, in order of authority.

    1. THE DIVISION SET, when it has an entry for this division name. It is
       Brio's own policy; a model that emits something else is not a reason to
       price differently.
    2. What the category declares, when the set has no entry — a category that
       is not one of the standard divisions. A declared 0 is 0, not "unset".
    3. 5%.

    ⚠️ ONE copy of this rule. Until 2026-10-12 the generator had it (fixed
    2026-10-06 after $2,895 of contingency on a $57,900 general-conditions
    line) while this module — the rollup every AI edit runs — still did
    `contingencyPct or 5`, so applying any edit to an estimate re-added 5% to
    divisions the set says carry none. The worker now calls this.
    """
    if by_division:
        pct = by_division.get((cat.get("name") or "").strip())
        if pct is not None:
            return float(pct)
    emitted = cat.get("contingencyPct")
    return float(5 if emitted is None else emitted)


def project_contingency(db: Any, project_id: Any) -> dict[str, float] | None:
    """The division set's contingency percentages for a project, by division
    name, or None for a project that resolves to no set."""
    from sqlalchemy import text as _T

    from app.services.divisions import division_context

    ptype = db.execute(_T("SELECT type FROM projects WHERE id = :p"),
                       {"p": str(project_id)}).scalar()
    ctx = division_context(db, ptype) or {}
    return ctx.get("contingency_pct") or None


def rollup_category(cat: dict[str, Any], by_division: dict[str, float] | None = None) -> None:
    """subtotalRaw / contingencyAmount / total for one category, in place.

    Idempotent: the existing contingency line is dropped before a new one is
    appended, so re-running does not stack contingency on contingency.
    """
    items = [
        it for it in (cat.get("items") or [])
        if isinstance(it, dict) and not it.get("contingency")
    ]
    if items:
        raw = sum(
            float(it.get("labor") or 0) + float(it.get("materials") or 0)
            for it in items
        )
    else:
        # standard / summary detail carries the category total directly with no
        # items[]. Summing an empty list here would zero out every category.
        raw = float(cat.get("total") or 0)

    cont_pct = contingency_pct_for(cat, by_division)
    cont = round(raw * cont_pct / 100)
    # A "Trade contingency @ 0%" line for $0 is noise on a client document.
    if items and cont:
        items.append({
            "name": f"Trade contingency @ {cont_pct}%",
            "qty": 1, "unitCost": cont, "labor": 0, "materials": cont,
            "laborHours": 0, "contingency": True,
        })
    if items:
        # Written back even when no new contingency line was added, so a stale
        # one from an earlier rollup does not survive a change to 0%.
        cat["items"] = items
    cat["subtotalRaw"] = round(raw)
    cat["contingencyAmount"] = cont
    cat["total"] = round(raw + cont)


def rollup_payload(payload: dict[str, Any],
                   by_division: dict[str, float] | None = None) -> dict[str, Any]:
    """Recompute every category, and the subtotal IF the payload carries one.

    Top-level totals are absent from most stored estimate payloads — they are
    derived at render time — so one is written back only where it already
    existed. Inventing a field the renderer does not read would put a number
    on the record that nothing checks.
    """
    changed: dict[str, Any] = {}
    cats = [c for c in (payload.get("categories") or []) if isinstance(c, dict)]
    for cat in cats:
        rollup_category(cat, by_division)
    if cats:
        changed["categories_rolled_up"] = len(cats)

    if "subtotal" in payload and cats:
        subtotal = round(sum(float(c.get("total") or 0) for c in cats))
        if subtotal != payload.get("subtotal"):
            payload["subtotal"] = subtotal
            changed["subtotal"] = subtotal
    return changed


GST_RATE = 0.05


def version_totals(payload: dict[str, Any] | None) -> dict[str, Any]:
    """What a version adds up to: the non-optional categories, then overhead,
    then profit, then GST. The one place this is computed for display — Home,
    the versions list and the version page all read it, so they cannot show
    three different totals for the same version.

    Category `total` already includes its contingency.
    """
    p = payload or {}
    cats = [c for c in (p.get("categories") or []) if isinstance(c, dict)]
    base = sum(float(c.get("total") or 0) for c in cats if not c.get("optional"))
    optional = sum(float(c.get("total") or 0) for c in cats if c.get("optional"))
    oh_pct = float(p.get("overhead") or 0)
    pr_pct = float(p.get("profit") or 0)
    overhead = base * oh_pct / 100
    profit = (base + overhead) * pr_pct / 100
    before_gst = round(base + overhead + profit)
    # Rounded so the displayed rows ADD UP: rounding each part on its own put
    # the total $1 away from its parts on a live estimate (430,003 shown under
    # 317,956 + 91,572 + 20,476). The remainder goes to profit and the total is
    # the sum, not a fresh rounding.
    subtotal = round(base)
    oh_r = round(overhead)
    gst = round(before_gst * GST_RATE)
    return {
        "subtotal": subtotal,
        "overhead_pct": oh_pct, "overhead": oh_r,
        "profit_pct": pr_pct, "profit": before_gst - subtotal - oh_r,
        "before_gst": before_gst,
        "gst": gst,
        "with_gst": before_gst + gst,
        "optional_subtotal": round(optional),
        "detail_level": p.get("detailLevel"),
    }
