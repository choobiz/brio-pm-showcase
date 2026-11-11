"""What rate does an estimate line imply, and does the library agree?

⚠️ READ THIS BEFORE CALLING ANY OF IT "PROVENANCE". It is not.

The honest statement of what can be known: the model WROTE the numbers on an
estimate line. It was shown the rate library as reference text and it priced
from its own reading of that. Nothing records which row it used, and nothing
can — short of making the model name one, which would be a prompt change and a
claim resting on the model's own report of itself.

So this module does not answer "which rate produced this line". It answers a
different question that is actually answerable:

    THIS LINE IMPLIES AN HOURLY RATE OF $X. THE LIBRARY SAYS $Y.

A line carrying `labor` and `laborHours` implies `labor / laborHours` as a wage
basis. That is arithmetic on the line itself, not an inference about intent, and
it is checkable by anyone. Where the two disagree, that disagreement is the
useful output — it is how you find a line priced at a billable rate when the
prompt demanded a cost basis, which is exactly the double-counting defect this
product has already had once.

⚠️ A LINE WITH NO HOURS IMPLIES NOTHING, and is reported as "cannot be derived"
rather than as agreeing. Most line items are lump sums; treating "no hours" as
"matches the library" would turn silence into endorsement across most of an
estimate.

Measured 2026-10-02 before building: 29 estimate versions exist, 3 carry a
category-level rate report, and ZERO line items name a rate. The assembly
library that `rate_reconcile` binds against holds 2 assemblies, so the
category path cannot cover much either. Labour (15 rows) and materials (43) are
the parts of the library with real content, which is why this works off them.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rate_library import LabourRate

#: Within this much of a library rate, the line is reported as agreeing. Wider
#: than float noise and narrower than a real pricing difference: a 5% gap on a
#: $45/hr wage is $2.25, which is rounding; a 20% gap is a different rate.
TOLERANCE_PCT = Decimal("5")

#: Words in a line's text that identify the trade, mapped to the library's own
#: `trade` values. Deliberately small and explicit — a fuzzy matcher that
#: guesses wrong attributes a line to a rate nobody used, and a WRONG
#: attribution is worse than none at all.
_TRADE_WORDS: dict[str, tuple[str, ...]] = {
    "carpenter": ("carpentry", "framing", "framer", "millwork", "trim", "finish carpentry"),
    "electrician": ("electrical", "wiring", "devices", "panel"),
    "plumber": ("plumbing", "drain", "waste", "vent", "fixture rough"),
    "drywaller": ("drywall", "gypsum", "taping"),
    "painter": ("paint", "painting", "coating"),
    # ⚠️ NOT the bare word "labour". Almost every labour line contains it, so
    # including it made nearly every line match two trades and fall through to
    # "no trade matched" — the matcher reported ambiguity about lines that were
    # perfectly clear. Only wording that actually identifies a labourer counts.
    "labourer": ("labourer", "general labour", "general labor",
                 "cleanup", "clean-up", "demolition"),
    "roofer": ("roof", "roofing", "shingle", "membrane"),
    "tiler": ("tile", "tiling"),
    "concrete finisher": ("concrete", "slab", "pour", "formwork"),
}


def _num(v: Any) -> Decimal | None:
    try:
        if v is None or v == "":
            return None
        d = Decimal(str(v))
        return d
    except Exception:  # noqa: BLE001
        return None


def implied_hourly(item: dict[str, Any]) -> Decimal | None:
    """`labor / laborHours`, or None when the line cannot imply a wage.

    None is a real answer and the caller must keep it distinct from "agrees".
    """
    labour = _num(item.get("labor"))
    hours = _num(item.get("laborHours"))
    if labour is None or hours is None or hours <= 0 or labour <= 0:
        return None
    return (labour / hours).quantize(Decimal("0.01"))


def trade_of(item: dict[str, Any], category_name: str | None = None) -> str | None:
    """The library trade this line is about, or None when it is not clear.

    None is returned freely. Guessing a trade in order to produce a comparison
    would attribute a line to a rate nobody used.
    """
    text = f"{item.get('name') or ''} {category_name or ''}".lower()
    hits = [t for t, words in _TRADE_WORDS.items() if any(w in text for w in words)]
    # Two trades named in one line means the line is not about one of them.
    return hits[0] if len(hits) == 1 else None


def _library(db: Session, project_type: str = "any") -> dict[str, LabourRate]:
    rows = db.execute(
        select(LabourRate).where(LabourRate.is_active.is_(True))
    ).scalars().all()
    best: dict[str, LabourRate] = {}
    for r in rows:
        # Apprentice rows describe supervised support hours and are not the
        # rate a scope is priced at. Excluded from comparison so a journeyman
        # line is never reported as "too expensive" against an apprentice wage.
        if "apprentice" in (r.trade or "").lower():
            continue
        if r.project_type not in ("any", project_type):
            continue
        prev = best.get(r.trade)
        if prev is None or (r.as_of_date or "") > (prev.as_of_date or ""):
            best[r.trade] = r
    return best


def check_line(
    db: Session, item: dict[str, Any], *, category_name: str | None = None,
    project_type: str = "any", library: dict[str, LabourRate] | None = None,
) -> dict[str, Any]:
    """One line, checked against the library. Never guesses."""
    lib = library if library is not None else _library(db, project_type)
    implied = implied_hourly(item)
    trade = trade_of(item, category_name)

    out: dict[str, Any] = {
        "name": item.get("name"),
        "implied_hourly": float(implied) if implied is not None else None,
        "trade": trade,
        "library_hourly": None,
        "library_source": None,
        "variance_pct": None,
        "verdict": "not derivable",
        "why": "",
    }

    if implied is None:
        out["why"] = (
            "This line carries no labour hours, so no hourly rate can be worked "
            "out from it. That is not agreement with the library — it is silence."
        )
        return out
    if trade is None:
        out["why"] = (
            f"This line implies ${implied}/hr, but its wording does not name one "
            f"trade clearly, so there is nothing to compare it against."
        )
        out["verdict"] = "no trade matched"
        return out

    rate = lib.get(trade)
    if rate is None:
        out["why"] = (
            f"This line implies ${implied}/hr for {trade}, and the library holds "
            f"no active rate for that trade."
        )
        out["verdict"] = "no library rate"
        return out

    lib_rate = Decimal(str(rate.effective_rate)).quantize(Decimal("0.01"))
    out["library_hourly"] = float(lib_rate)
    out["library_source"] = rate.source
    if lib_rate <= 0:
        out["verdict"] = "no library rate"
        out["why"] = f"The library's {trade} rate is not usable."
        return out

    variance = ((implied - lib_rate) / lib_rate * 100).quantize(Decimal("0.1"))
    out["variance_pct"] = float(variance)
    if abs(variance) <= TOLERANCE_PCT:
        out["verdict"] = "agrees"
        out["why"] = (
            f"${implied}/hr against the library's ${lib_rate}/hr for {trade}."
        )
    else:
        direction = "above" if variance > 0 else "below"
        out["verdict"] = "differs"
        out["why"] = (
            f"${implied}/hr is {abs(variance)}% {direction} the library's "
            f"${lib_rate}/hr for {trade} ({rate.source}). The prompt asks for a "
            f"cost basis; a rate well above the library may be a billable rate, "
            f"which margin is then applied to a second time."
        )
    return out


def check_estimate(
    db: Session, payload: dict[str, Any], *, project_type: str = "any",
) -> dict[str, Any]:
    """Every line in an estimate, checked. Counts are the point of the summary:
    a reviewer needs to know how much of the estimate this could speak to at
    all before reading any individual verdict."""
    lib = _library(db, project_type)
    lines: list[dict[str, Any]] = []
    for cat in payload.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        cname = cat.get("name")
        for item in cat.get("items") or []:
            if not isinstance(item, dict) or item.get("contingency"):
                continue
            row = check_line(db, item, category_name=cname,
                             project_type=project_type, library=lib)
            row["category"] = cname
            lines.append(row)

    counts: dict[str, int] = {}
    for r in lines:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    checkable = counts.get("agrees", 0) + counts.get("differs", 0)
    return {
        "lines": lines,
        "total_lines": len(lines),
        "checkable": checkable,
        "counts": counts,
        "library_trades": sorted(lib.keys()),
        # Stated in the response so no caller can present this as provenance.
        "caveat": (
            "This does not record which rate produced a line — nothing does. It "
            "works out the hourly rate each line implies and compares it with the "
            "library. Lines without labour hours cannot be checked at all."
        ),
    }
