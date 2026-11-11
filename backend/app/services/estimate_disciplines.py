"""Which trades this estimate actually needs.

`estimate_full` runs one AI call per discipline and there are six of them, so a
run costs six calls and several minutes whatever the job is. A partition wall
with one photo paid for a plumbing pass and a mechanical pass that had nothing
to price. That is the whole of the "why does a small job take ten minutes"
question: the cost is driven by the number of trades walked, not by how many
documents were attached.

The recommendation is derived from the scope's own category names and item text,
and every discipline comes back with the words that triggered it. It is a
RECOMMENDATION, deliberately: an automatic router that quietly drops a trade
produces a confidently incomplete estimate, and nobody reviewing the total would
see the omission. A person confirms the set.

architectural and gc_rollup are always included. Practically every construction
scope has work and overhead in it, and their absence is far more likely to be a
gap in the scope text than a genuine absence of the trade.
"""
from typing import Any

ALL_DISCIPLINES = [
    "architectural", "structural", "mechanical", "electrical", "plumbing", "gc_rollup",
]

ALWAYS = ["architectural", "gc_rollup"]

# Deliberately generous. A false positive costs one extra AI call; a false
# negative costs a trade missing from a price someone sends to a client.
HINTS: dict[str, list[str]] = {
    "structural": [
        "structur", "framing", "stud", "foundation", "footing", "beam", "joist",
        "lintel", "shoring", "concrete", "steel", "load-bearing", "load bearing",
        "post", "header", "truss", "slab",
    ],
    "mechanical": [
        "hvac", "mechanical", "ventilat", "furnace", "heat pump", "duct",
        "exhaust fan", "air handling", "make-up air", "rooftop unit",
    ],
    "electrical": [
        "electric", "lighting", "light fixture", "pot light", "receptacle",
        "outlet", "switch", "panel", "breaker", "wiring", "circuit", "data",
        "low voltage", "fire alarm",
    ],
    "plumbing": [
        "plumb", "drain", "water line", "supply line", "waste", "vent stack",
        "fixture rough", "sink", "toilet", "shower", "tub", "faucet",
        "hot water tank", "backflow",
    ],
}


def _haystack(scope_payload: dict[str, Any]) -> list[str]:
    """Every piece of scope text worth matching, lowercased."""
    out: list[str] = []
    for cat in scope_payload.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        if cat.get("name"):
            out.append(str(cat["name"]).lower())
        for item in cat.get("items") or []:
            text = item.get("description") or item.get("name") if isinstance(item, dict) else item
            if text:
                out.append(str(text).lower())
    for key in ("summary", "assumptions", "exclusions", "materials"):
        val = scope_payload.get(key)
        if isinstance(val, str):
            out.append(val.lower())
        elif isinstance(val, list):
            out.extend(str(v).lower() for v in val)
    return out


def recommend_disciplines(scope_payload: dict[str, Any] | None) -> dict[str, Any]:
    """Suggest a discipline set, with the evidence for each one.

    With no scope to read, everything is recommended: guessing narrow on no
    information is how a trade goes missing.
    """
    if not scope_payload:
        return {
            "recommended": list(ALL_DISCIPLINES),
            "reasons": {d: ["no scope to read, so nothing is ruled out"] for d in ALL_DISCIPLINES},
            "skipped": [],
            "basis": "none",
        }

    text = _haystack(scope_payload)
    recommended = list(ALWAYS)
    reasons: dict[str, list[str]] = {
        "architectural": ["always included"],
        "gc_rollup": ["always included"],
    }

    for discipline, words in HINTS.items():
        hits = sorted({w for w in words for line in text if w in line})
        if hits:
            recommended.append(discipline)
            reasons[discipline] = [f"scope mentions {', '.join(hits[:4])}"]

    ordered = [d for d in ALL_DISCIPLINES if d in recommended]
    skipped = [d for d in ALL_DISCIPLINES if d not in recommended]
    return {
        "recommended": ordered,
        "reasons": reasons,
        "skipped": skipped,
        "basis": "scope",
    }
