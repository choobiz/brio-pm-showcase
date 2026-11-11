"""A client package must build for a project that has no schedule yet.

456 W Sample, 2026-10-27: downloading the client package returned 500
(NoResultFound): build_client_package loaded the latest schedule with .one(),
and a project priced before it is scheduled has none.

Now: the package builds without the schedule section, the cover shows no dates,
and the contract says the dates are agreed in writing before the Work starts
(Schedule D kept, so Schedules E-G keep their letters) instead of printing
"None". Asking for the schedule document alone, or for a package with no
current estimate, is a clear 409, never a 500.
"""
from __future__ import annotations

import fitz
import pytest

from app.services import package_pdf as P

PID = "dffc01b2-9f8a-4ed3-89ec-f6e43e140c13"
EST = {"categories": [{"name": "09 Finishes", "total": 100000, "items": [
    {"name": "Carpet", "labor": 20000, "materials": 80000}]}], "overhead": 12, "profit": 15}


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def one(self):
        if len(self.rows) != 1:
            raise AssertionError("one() on %d rows: this is the 500" % len(self.rows))
        return self.rows[0]

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def scalar(self):
        return self.rows[0] if self.rows else None


class FakeDB:
    """Answers the builder's queries for a project with an estimate and no schedule."""

    def __init__(self, active=True):
        self.active = active

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "from estimate_versions" in sql:
            return _Result([("456SA-SUITE-425", EST)] if self.active else [])
        if "from projects" in sql:
            return _Result([{"name": "456 W Sample", "address": "456 W Sample St", "type": "commercial_ti",
                             "client_id": None, "slug": "456-w-sample", "summary": "",
                             "discussion_points": [], "home_warranty_required": False, "sqft": 687}])
        if "from schedules" in sql or "from documents" in sql or "from change_orders" in sql:
            return _Result([])
        if "from contracts" in sql:
            return _Result([])
        raise AssertionError(f"unexpected query: {sql[:80]}")


def _text(pdf: bytes) -> str:
    doc = fitz.open(stream=pdf, filetype="pdf")
    return "\n".join(p.get_text() for p in doc)


def test_the_package_builds_without_a_schedule():
    pdf, name = P.build_client_package(FakeDB(), PID, "active", "merged")
    text = _text(pdf)
    assert name.endswith("Client-Package.pdf")
    assert "None" not in text, "a missing date must never print as None"
    assert "agreed in writing by both parties before the Work starts" in text   # Article A-5
    assert "Schedule D" in text and "Schedule E" in text                         # letters unchanged
    assert "CPM critical path" not in text


def test_the_standalone_contract_builds_without_a_schedule():
    pdf, _ = P.build_client_package(FakeDB(), PID, "active", "contract")
    assert "recorded in Schedule D" in _text(pdf)


def test_asking_for_the_schedule_alone_is_a_clear_refusal():
    with pytest.raises(P.PackageIncomplete, match="no schedule"):
        P.build_client_package(FakeDB(), PID, "active", "schedule")


def test_no_current_estimate_is_a_clear_refusal():
    with pytest.raises(P.PackageIncomplete, match="no current estimate"):
        P.build_client_package(FakeDB(active=False), PID, "active", "merged")


def test_the_schedule_section_is_left_out_only_when_there_is_none():
    assert "schedule" in P.package_order(True)
    assert "schedule" not in P.package_order(False)


def test_the_contract_keeps_its_dates_when_there_is_a_schedule():
    pdf = P.render_contract({"name": "X", "address": "1 Main"}, {}, 1000, 50, 1050,
                            [{"milestone": "Deposit", "pct": 100, "amount": 1050}],
                            "2027-01-04", "2027-03-26", {"estimate": "E", "cashflow": "CF", "schedule": "SCH"},
                            cover=False)
    text = _text(pdf)
    assert "2027-01-04" in text and "2027-03-26" in text and "CPM critical path" in text
