"""Describe-a-change must see an estimate's assumptions (rollout test, 2026-10-15).

On ZZ estimate v2, "change the door hardware finish from satin chrome to matte
black" changed nothing: the field list stopped at 400 lines, in document order,
and the 66 assumptions (where "satin chrome" lived) came after the line items.
"""
from app.services.edit_plan import MAX_PATH_LINES, describe


def _estimate(n_items=200, n_assumptions=66):
    return {
        "categories": [{"name": f"Div {i}", "items": [
            {"name": f"Item {i}.{j}", "qty": 1, "unit": "ea", "labor": 10, "materials": 5, "total": 15}
            for j in range(n_items // 10)]} for i in range(10)],
        "assumptions": [f"Assumption {k}" for k in range(n_assumptions - 1)]
                       + ["Door hardware assumed: passage sets in standard satin chrome"],
        "exclusions": ["Permits by owner"],
    }


def test_assumptions_are_listed_even_when_the_items_overflow_the_cap():
    doc = _estimate(n_items=2000)
    text = describe(doc)
    assert "satin chrome" in text and "more fields not listed" in text


def test_prose_comes_before_the_line_items():
    text = describe(_estimate())
    assert text.index("assumptions[0]") < text.index("categories[0]")


def test_the_cap_is_large_enough_for_a_typical_estimate():
    assert MAX_PATH_LINES >= 800
