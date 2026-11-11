"""Derive a scope-of-work document FROM an estimate version.

After the first estimate (which is built bottom-up from documents + CAD takeoff
+ verified rates), the ESTIMATE is the source of truth. When a new estimate
version is activated, its scope is re-derived here so the scope always matches
what is actually in the estimate — no stale, removed, or resized items linger.

Grounding: describe ONLY work present in the estimate. Never add scope,
materials, or systems the estimate doesn't contain; carry its exclusions
verbatim.
"""
from __future__ import annotations

import json
from typing import Any

_SYSTEM = """You are a senior estimator at BRIO writing the SCOPE OF WORK that corresponds to a finalized construction estimate. The ESTIMATE is the single source of truth.

ABSOLUTE RULES:
- Describe ONLY work that appears in the estimate below. Every scope category and item must correspond to estimate content. Do NOT add, infer, or reinstate any work, material, system, or size that is not in the estimate (e.g. if there is no HVAC/split-system line, the scope must not mention one).
- Carry the estimate's exclusions and assumptions across VERBATIM where provided.
- You may phrase items in plain, client-readable language, but you must not change quantities, sizes, or systems.
- No dollar figures in the scope.

Produce a scope-of-work JSON with this exact shape (JSON only, no prose/fences):
{
  "reference": "<short label>",
  "date": "<YYYY-MM-DD>",
  "summary": "<3-5 flowing sentences: what is being built and the one or two most significant constraints>",
  "categories": [ { "name": "<category name, mirroring the estimate categories>", "items": ["<specific work item present in the estimate>", "..."] } ],
  "materials": [ { "item": "<material named in the estimate>", "spec": "<spec or 'TBD — client to select'>", "notes": "<allowance / lead time>" } ],
  "potentialIssues": ["<risk grounded in the estimate scope>"],
  "accessConstraints": ["<site/delivery constraint if evident>"],
  "permits": ["<permit type + reason, only if evident>"],
  "assumptions": ["<assumption from the estimate>"],
  "exclusions": ["<exclusion from the estimate, verbatim>"]
}
Escape all quotes/newlines inside strings so the JSON parses."""


def build_scope_from_estimate_messages(estimate_payload: dict, project: dict) -> tuple[str, list[dict], int]:
    """Returns (system, messages, max_tokens) for deriving a scope from an estimate."""
    cats = []
    for c in estimate_payload.get("categories") or []:
        if not isinstance(c, dict):
            continue
        cats.append({
            "name": c.get("name"),
            "description": c.get("description"),
            "items": [it.get("name") for it in (c.get("items") or []) if isinstance(it, dict) and it.get("name")],
        })
    payload = {
        "project": {"name": project.get("name"), "address": project.get("address"),
                    "type": project.get("type"), "sqft": project.get("sqft")},
        "estimateCategories": cats,
        "assumptions": estimate_payload.get("assumptions") or [],
        "exclusions": estimate_payload.get("exclusions") or [],
    }
    user = ("Write the scope of work that matches this estimate. Use ONLY what is present here.\n\n"
            + json.dumps(payload, indent=1, default=str))
    return _SYSTEM, [{"role": "user", "content": user}], 8000


def stamp(parsed: dict) -> dict:
    """Fill reference/date if the model omitted them."""
    parsed = dict(parsed or {})
    from app.services.home import today

    # Always the day it was written: a model-supplied date is invented (2026-10-14).
    parsed["date"] = today().isoformat()
    if not parsed.get("reference"):
        parsed["reference"] = "SCOPE-FROM-EST"
    return parsed
