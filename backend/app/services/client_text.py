"""Text bound for a client document.

Internal pricing structure must never reach a client. The estimate carries
contingency, overhead and profit as line detail because that is how the number
is built; the client is shown scope and a price. The estimate renderer has
always folded overhead and profit into each category total for exactly this
reason, but the SCOPE TEXT was never filtered, so the structure arrived anyway
in the wording of the items.

Found 2026-09-22 on invoice INV-2026-10056, which carried eight
"Trade contingency @ 0%" lines and two lines naming the GC/PM rate band. The
same leak was then confirmed in the rendered scope document.

Two shapes leak, and they need different treatment:

  · a line that is only an estimating artifact   -> drop the line
  · a real scope line with an internal clause    -> keep the scope, cut the
    clause, because the work described is genuine and dropping it would
    understate what was done

Deliberately NOT removed: a contractual statement such as "a contingency for
unforeseen conditions is included in the price; unused contingency is not
refunded". That is a commercial term that protects BRIO, not a disclosure of
internal structure, and it carries no percentage. It survives because it is a
full sentence rather than an artifact line, and has no dash-introduced clause.
"""
import re

_INTERNAL_TERMS = r"conting\w*|overhead|profit|margins?|mark[- ]?ups?|o\s*&\s*p|oh\s*&\s*p"

# A line that is nothing but an estimating artifact, with or without a
# percentage. The chain alternation catches "Overhead and profit", "OH & P".
PRICING_ONLY = re.compile(
    rf"^\s*[-•]*\s*(?:trade\s+|general\s+|project\s+)?(?:{_INTERNAL_TERMS})\b"
    rf"(?:\s*(?:and|&|/|,|\+|—|–)\s*(?:{_INTERNAL_TERMS})\b)*"
    r"[^A-Za-z]*(?:@?\s*[\d.]+\s*%)?\s*$",
    re.I,
)

# A trailing clause, introduced by a dash, explaining how money was allocated
# rather than what was done.
PRICING_CLAUSE = re.compile(
    rf"\s*[—–]\s*[^—–]*\b(?:{_INTERNAL_TERMS})\b.*$",
    re.I,
)


def redact_internal_pricing(items: list[str]) -> list[str]:
    """Remove estimating structure from text bound for a client document."""
    out: list[str] = []
    for item in items:
        if PRICING_ONLY.match(item):
            continue
        cleaned = PRICING_CLAUSE.sub("", item).rstrip(" ,;:-—–")
        # Only keep the trimmed version if something readable survived.
        if len(cleaned) >= 12:
            out.append(cleaned)
        elif not PRICING_CLAUSE.search(item):
            out.append(item)
    return out
