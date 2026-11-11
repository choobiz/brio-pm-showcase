"""Checking estimate lines against the rate library.

The property that matters most is what this module REFUSES to say. It cannot
know which rate produced a line — the model wrote the number — so every path
that cannot produce a real comparison must return a distinct verdict rather than
falling through to "agrees". Silence presented as endorsement would be worse
than no check at all, because most lines are lump sums and most of an estimate
would quietly read as library-approved.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text as _T

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services import line_rates as LR  # noqa: E402


def _labour(db, trade, rate, *, source="job_bank", active=True, ptype="any"):
    db.execute(_T("""insert into labour_rates
        (id, trade, region, project_type, source, base_rate, discount_pct,
         effective_rate, is_active)
        values (:i,:t,'metro-vancouver',:pt,:s,:r,0,:r,:a)"""),
        {"i": str(uuid4()), "t": trade, "pt": ptype, "s": source,
         "r": rate, "a": active})


def _item(name, labor=None, hours=None, **kw):
    d = {"name": name, "labor": labor, "laborHours": hours}
    d.update(kw)
    return d


# --- what it refuses to say -------------------------------------------------

def test_a_line_with_no_hours_is_not_derivable_never_agreeing(db_session):
    """Most lines are lump sums. Treating 'no hours' as agreement would make
    most of an estimate read as library-approved without a single check."""
    _labour(db_session, "carpenter", 48)
    out = LR.check_line(db_session, _item("Framing allowance", labor=5000, hours=None))
    assert out["verdict"] == "not derivable"
    assert out["implied_hourly"] is None
    assert "silence" in out["why"]


@pytest.mark.parametrize("hours", [0, -2, None, "", "abc"])
def test_unusable_hours_cannot_imply_a_rate(db_session, hours):
    assert LR.implied_hourly(_item("x", labor=1000, hours=hours)) is None


def test_a_line_naming_no_trade_is_reported_as_such(db_session):
    _labour(db_session, "carpenter", 48)
    out = LR.check_line(db_session, _item("Miscellaneous site works", labor=480, hours=10))
    assert out["verdict"] == "no trade matched"
    assert out["implied_hourly"] == 48.0, "the arithmetic still runs"
    assert out["library_hourly"] is None


def test_a_line_naming_two_trades_is_not_attributed_to_either(db_session):
    """A wrong attribution is worse than none: it credits a rate nobody used."""
    _labour(db_session, "carpenter", 48)
    _labour(db_session, "painter", 40)
    out = LR.check_line(db_session, _item("Framing and painting", labor=480, hours=10))
    assert out["verdict"] == "no trade matched"


def test_a_trade_with_no_library_rate_says_so(db_session):
    out = LR.check_line(db_session, _item("Roofing membrane labour", labor=500, hours=10))
    assert out["verdict"] == "no library rate"
    assert out["implied_hourly"] == 50.0


# --- what it does say -------------------------------------------------------

def test_a_line_within_tolerance_agrees(db_session):
    _labour(db_session, "carpenter", 48)
    out = LR.check_line(db_session, _item("Wall framing labour", labor=490, hours=10))
    assert out["verdict"] == "agrees"
    assert out["library_hourly"] == 48.0
    assert abs(out["variance_pct"]) <= float(LR.TOLERANCE_PCT)


def test_a_line_well_above_the_library_is_flagged_with_the_reason(db_session):
    """The defect this exists to catch: a billable rate where a cost basis was
    asked for, which margin is then applied to a second time."""
    _labour(db_session, "carpenter", 48)
    out = LR.check_line(db_session, _item("Framing labour", labor=900, hours=10))
    assert out["verdict"] == "differs"
    assert out["variance_pct"] > 80
    assert "billable" in out["why"]


def test_a_line_below_the_library_is_also_flagged(db_session):
    _labour(db_session, "drywaller", 40)
    out = LR.check_line(db_session, _item("Drywall taping labour", labor=200, hours=10))
    assert out["verdict"] == "differs"
    assert out["variance_pct"] < 0
    assert "below" in out["why"]


# --- library selection ------------------------------------------------------

def test_apprentice_rates_are_never_the_comparison(db_session):
    """An apprentice rate covers supervised support hours. Comparing a
    journeyman line against it would report honest pricing as excessive."""
    _labour(db_session, "carpenter", 48)
    _labour(db_session, "carpenter-apprentice", 22)
    lib = LR._library(db_session)
    assert "carpenter" in lib
    assert not any("apprentice" in t for t in lib)


def test_inactive_rates_are_ignored(db_session):
    _labour(db_session, "painter", 40, active=False)
    out = LR.check_line(db_session, _item("Painting labour", labor=400, hours=10))
    assert out["verdict"] == "no library rate"


# --- whole estimate ---------------------------------------------------------

def test_an_estimate_reports_how_much_it_could_check_at_all(db_session):
    """A reviewer needs the coverage before any individual verdict — "4 of 30
    lines could be checked" changes how the four are read."""
    _labour(db_session, "carpenter", 48)
    payload = {"categories": [{
        "name": "Rough carpentry",
        "items": [
            _item("Wall framing labour", labor=490, hours=10),     # agrees
            _item("Framing labour premium", labor=900, hours=10),  # differs
            _item("Lumber supply", labor=None, hours=None),        # not derivable
            _item("Trade contingency @ 5%", contingency=True),     # skipped entirely
        ],
    }]}
    out = LR.check_estimate(db_session, payload)
    assert out["total_lines"] == 3, "the contingency line is not an estimate line"
    assert out["checkable"] == 2
    assert out["counts"]["agrees"] == 1
    assert out["counts"]["differs"] == 1
    assert out["counts"]["not derivable"] == 1


def test_the_response_states_that_this_is_not_provenance(db_session):
    """No caller should be able to present this as "which rate produced it"."""
    out = LR.check_estimate(db_session, {"categories": []})
    assert "does not record which rate produced a line" in out["caveat"]
