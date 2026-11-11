"""Internal wording stays out of what a client reads (rollout test, 2026-10-15).

ZZ Rollout Test's estimate v2 carried the assumption "PM and General Conditions
is priced as an overhead allocation ... The $15-40k exterior and $5-15k design
ranges from the calibration rule were scaled down". The client PDF's filter
knew only "contingency", "owner audit adjustment" and "mobilization", and the
app's Client's view applied no filter at all.
"""
import re
from pathlib import Path

from app.services import estimate_pdf as E

LEAKED = ("PM and General Conditions is priced as an overhead allocation, with one shared supervisor. "
          "The $15-40k exterior and $5-15k design ranges from the calibration rule were scaled down.")
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_the_client_pdf_drops_internal_notes():
    kept = E._client_filter_bullets([LEAKED, "Interior doors assumed pre-hung solid core.",
                                     "Verified door_count of 3 ea (cad_extract) used for pricing"])
    assert kept == ["Interior doors assumed pre-hung solid core."]


def test_names_are_not_dropped_by_the_wider_note_list():
    """A category NAME that matches loses its money from the client edition, so
    the note list must not reach names: "Overhead Doors" is a real line."""
    assert not E._client_redact("Overhead Doors")
    assert E._client_redact("Overhead Doors", E._CLIENT_NOTE_TERMS)


def test_the_app_client_view_uses_the_same_list():
    src = (FRONTEND / "lib" / "clientRedact.ts").read_text()
    block = re.search(r"CLIENT_REDACT_TERMS = \[(.*?)\];", src, re.S).group(1)
    assert tuple(re.findall(r'"([^"]+)"', block)) == E._CLIENT_NOTE_TERMS
    table = (FRONTEND / "app" / "(authed)" / "projects" / "[id]" / "estimate" / "[vid]" / "DivisionTable.tsx").read_text()
    assert "assumptions = clientSafe(assumptions)" in table and "exclusions = clientSafe(exclusions)" in table


def test_an_item_name_loses_an_internal_bracket_but_keeps_the_item():
    """ZZ v2's client PDF listed "PM / site supervision (overhead allocation, one
    shared supervisor over about 1.5 weeks)"."""
    assert E._client_item_name("PM / site supervision (overhead allocation, one shared supervisor)") \
        == "PM / site supervision"
    assert E._client_item_name("Overhead door, 9x7 insulated (supply and install)") \
        == "Overhead door, 9x7 insulated (supply and install)"
    assert E._client_item_name("Mobilization") is None
