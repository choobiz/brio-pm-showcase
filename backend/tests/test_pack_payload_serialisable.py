"""The analysis pack is stored as jsonb, so its payload must be JSON.

This was found in production, not here: `audit_set` builds its per-building
summary with sets, which are the natural structure for it and which psycopg
refuses at the driver — "Object of type set is not JSON serializable". Every
test around the pack had built the payload and ROLLED BACK, so nothing ever
serialised it, and "Run analysis" failed with a 500 the first time a person
pressed it.
"""
import json

from app.services.drawing.set_completeness import Sheet, audit_set


def _payload(audit):
    """The shape build_pack stores under `set_audit`."""
    return {"complete": audit.complete, "findings": audit.findings,
            "by_building": audit.by_building}


def test_the_set_audit_survives_json():
    sheets = [
        Sheet("107 Maple - A6 - BASEMENT FLOOR PLAN.dwg", "107", ("A", 6),
              "floor_plan", storeys_covered=["basement"]),
        Sheet("107 Maple - A14 - BUILDING SECTIONS.dwg", "107", ("A", 14),
              "section", storeys_covered=[]),
        Sheet("109 Maple - A6 - BASEMENT _ LEVEL 1 FLOOR PLANS.dwg", "109",
              ("A", 6), "floor_plan", storeys_covered=["basement", "level 1"]),
    ]
    out = json.dumps(_payload(audit_set(sheets)))     # must not raise
    assert "by_building" in out


def test_a_single_sheet_set_survives_json():
    """1234 is one unclassified-then-plan sheet, and it is the case that failed."""
    audit = audit_set([Sheet("1234 Example - Plan.dxf", "1234", None,
                             "floor_plan", storeys_covered=[])])
    json.dumps(_payload(audit))


def test_no_set_survives_into_the_audit_at_all():
    """Guard the general property rather than the three keys that happened to
    hold sets, so a fourth added later fails here and not in production."""
    audit = audit_set([Sheet("A6 - BASEMENT.dwg", "default", ("A", 6),
                             "floor_plan", storeys_covered=["basement"])])

    def walk(o, path="by_building"):
        assert not isinstance(o, (set, frozenset)), f"set at {path}"
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}")
        elif isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")

    walk(audit.by_building)
